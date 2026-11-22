from datetime import date
from fastapi import HTTPException
from sqlalchemy import select,func
from .models import (Accession,Lot,Regeneration,Flowering,Pollination,Plot,Emergence,Resow)
from .common import lock,pack,add,audit,reserve
from .constants import EMERGENCE_MIN_SAMPLES
from .emergence_rate import fit_interval,needed_by_lower_bound

def _crop_of(s,regen):
    return s.get(Accession,s.get(Lot,regen.lot_id).accession_id).crop

def _history(s,crop,exclude_regen_id=None):
    # 同作物历次：每次已登记出苗的下田复壮留一条（播量、播期、出苗数→出苗率）。
    rows=[]
    for e in s.scalars(select(Emergence).order_by(Emergence.id)).all():
        if e.regeneration_id==exclude_regen_id:continue
        r=s.get(Regeneration,e.regeneration_id)
        if _crop_of(s,r)!=crop:continue
        doy=date.fromisoformat(e.sowing_date).timetuple().tm_yday
        rows.append({'sown':e.sown,'doy':doy,'emerged':e.emerged,
                     'rate_pct':round(100*e.emerged/e.sown,3)})
    return rows

def _latest_emergence(s,regen_id):
    return s.scalars(select(Emergence).where(Emergence.regeneration_id==regen_id).order_by(Emergence.id.desc())).first()

def estimate(s,regeneration_id,target_plants,sowing_doy=None):
    r=lock(s,Regeneration,regeneration_id)
    e=_latest_emergence(s,r.id)
    if not e:raise HTTPException(409,'先登记本单出苗数，才能拟合出苗率区间')
    doy=sowing_doy or date.fromisoformat(e.sowing_date).timetuple().tm_yday
    rows=_history(s,_crop_of(s,r),r.id)
    fit=fit_interval(rows,doy,EMERGENCE_MIN_SAMPLES)
    fit['needed_seeds_lower_bound']=needed_by_lower_bound(target_plants,fit['lower_pct'])
    return fit

def record_emergence(s,d,u):
    r=lock(s,Regeneration,d.regeneration_id)
    if r.state!='issued':raise HTTPException(409,'只有已下田（已领种未收获）的复壮单可登记出苗')
    f=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    if not f or f.state!='assigned':raise HTTPException(409,'先核准隔离排期并下田，再登记出苗数')
    if d.sown!=r.issued:raise HTTPException(409,'播量须与本单实际领种下田粒数（'+str(r.issued)+'粒，含重播补领）一致')
    x=_latest_emergence(s,r.id)
    if x and x.state=='recorded':
        if s.scalar(select(Resow.id).where(Resow.emergence_id==x.id,Resow.state=='pending_review')):
            raise HTTPException(409,'重播申请待主管确认，先处理申请后再改出苗数')
        x.code=d.code;x.sown=d.sown;x.emerged=d.emerged;x.sowing_date=d.sowing_date.isoformat()
        s.flush();audit(s,u,'修订出苗登记 '+d.code);return pack(x)
    # 无在册记录，或上一轮已随换地重播置superseded：为本次下田新建一条出苗档案。
    x=Emergence(regeneration_id=r.id,code=d.code,sown=d.sown,emerged=d.emerged,
                sowing_date=d.sowing_date.isoformat(),state='recorded')
    audit(s,u,'登记下田出苗 '+d.code);return add(s,x)

def apply_resow(s,d,u):
    r=lock(s,Regeneration,d.regeneration_id)
    if r.state!='issued':raise HTTPException(409,'复壮单已结束，不能申请换地重播')
    p=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id,Pollination.active==True))
    if p and p.state=='pollinated':raise HTTPException(409,'原单授粉已先完成，拒绝换地重播')
    e=_latest_emergence(s,r.id)
    if not e or e.state!='recorded':raise HTTPException(409,'先登记本轮出苗缺苗数据，再申请换地重播')
    plot=s.get(Plot,d.plot_id)
    if not plot:raise HTTPException(404,'隔离小区不存在')
    old=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    if old and plot.id==old.plot_id:raise HTTPException(409,'换地重播必须指定与原地不同的小区')
    if d.target_plants>plot.capacity:raise HTTPException(409,'目标株数超过新地块容量（'+str(plot.capacity)+'株）')
    if s.scalar(select(Resow.id).where(Resow.regeneration_id==r.id,Resow.state=='pending_review')):
        raise HTTPException(409,'本单已有待确认的重播申请，不可重复申请')
    fit=estimate(s,r.id,d.target_plants,d.start.timetuple().tm_yday)
    x=Resow(code=d.code,regeneration_id=r.id,emergence_id=e.id,plot_id=plot.id,
            start=d.start.isoformat(),end=d.end.isoformat(),target_plants=d.target_plants,
            needed_seeds=fit['needed_seeds_lower_bound'],estimate_snapshot=fit,
            state='pending_review')
    audit(s,u,'申请换地重播 '+d.code+('（样本不足待核）' if not fit['reliable'] else ''))
    return add(s,x)

def _flower_conflict(s,plots,crop,plot_id,start,end,ignore_regen_id):
    # 与 isolation.assign 同口径：同地块任何作物花期重叠拒；正交邻接同作物重叠拒，首尾日均含。
    target=next(p for p in plots if p.id==plot_id)
    for other in s.scalars(select(Flowering).where(Flowering.state=='assigned')).all():
        if other.regeneration_id==ignore_regen_id:continue
        if not (start<=other.end and end>=other.start):continue
        neighbour=next(p for p in plots if p.id==other.plot_id)
        if plot_id==neighbour.id:return '同地块花期重叠：'+neighbour.code+' '+other.start+'至'+other.end
        near=abs(target.row-neighbour.row)+abs(target.position-neighbour.position)<=1
        if near and crop==_crop_of(s,s.get(Regeneration,other.regeneration_id)):
            return '相邻同作物花期重叠：'+neighbour.code+' '+other.start+'至'+other.end
    return None

def reject_resow(s,id,u,reason='主管核定留原地，不换地重播'):
    pre=s.get(Resow,id)
    if not pre:raise HTTPException(404,'重播申请不存在')
    x=lock(s,Resow,id);r=lock(s,Regeneration,pre.regeneration_id)
    if x.state!='pending_review':raise HTTPException(409,'重播申请已处理，不可重复操作')
    x.state='rejected';x.reject_reason=reason
    audit(s,u,'驳回换地重播（主管留原地） '+x.code);return pack(x)

def confirm_resow(s,id,u,approved_seeds=None):
    pre=s.get(Resow,id)
    if not pre:raise HTTPException(404,'重播申请不存在')
    # 与 isolation.assign / pollinate 同一锁序：全部地块（按ID）→ 申请行 → 复壮根行 → 种质父行，
    # 先锁申请行使并发的第二次确认直接在状态守卫处等待/失败，杜绝重复扣种。
    plots=s.scalars(select(Plot).order_by(Plot.id).with_for_update()).all()
    x=lock(s,Resow,id)
    r=lock(s,Regeneration,pre.regeneration_id)
    if x.state!='pending_review':raise HTTPException(409,'重播申请已处理，不可重复确认（重试不重复扣种）')
    lot=s.get(Lot,r.lot_id);acc=lock(s,Accession,lot.accession_id)
    # 并发裁决：授粉先成就拒绝重播，旧袋签原样保留。
    p=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id,Pollination.active==True))
    if p and p.state=='pollinated':
        x.state='rejected';x.reject_reason='原单授粉已先完成，拒绝换地重播'
        audit(s,u,'驳回换地重播（授粉先成） '+x.code);return pack(x)
    if r.state!='issued':
        x.state='rejected';x.reject_reason='原复壮单已结束，留原地'
        audit(s,u,'驳回换地重播（复壮已结束） '+x.code);return pack(x)
    seeds=approved_seeds
    if seeds is None:
        if not x.estimate_snapshot['reliable'] or x.needed_seeds is None:
            raise HTTPException(409,'同作物样本不足，需主管人工核定重播领种粒数')
        seeds=x.needed_seeds
    # 保底预检：不足则留原地，申请驳回且不扣一粒种。
    total=int(s.scalar(select(func.sum(Lot.available)).where(Lot.accession_id==acc.id)) or 0)
    if seeds>lot.available or total-seeds<acc.minimum:
        x.state='rejected';x.reject_reason='原批余量或保底不足（需'+str(seeds)+'粒），留原地'
        audit(s,u,'驳回换地重播（保底不足） '+x.code);return pack(x)
    # 花期冲突预检：冲突则留原地。
    conflict=_flower_conflict(s,plots,_crop_of(s,r),x.plot_id,x.start,x.end,r.id)
    if conflict:
        x.state='rejected';x.reject_reason='新排期'+conflict+'，留原地'
        audit(s,u,'驳回换地重播（花期冲突） '+x.code);return pack(x)
    # 通过：从原批实际扣种（reserve 再校验一次，失败整体回滚，申请仍待确认）。
    f=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    issued_before=r.issued
    snapshot=reserve(s,lot,seeds)
    x.approved_seeds=seeds;x.reserve_snapshot=snapshot;x.state='approved'
    # 旧种已下田不返库；原地释放、旧袋签停用；同一复壮单花期行就地改为新排期（唯一约束）。
    old_schedule={'plot_id':f.plot_id,'start':f.start,'end':f.end} if f else None
    if f:f.plot_id=x.plot_id;f.start=x.start;f.end=x.end;f.state='assigned'
    if p:
        p.active=False
        audit(s,u,'停用旧授粉袋签 '+p.bag_code)
    em=lock(s,Emergence,x.emergence_id);em.state='superseded'
    r.issued=issued_before+seeds   # 重播用种并入本单下田粒数，作为新授粉花数上限
    x.estimate_snapshot=x.estimate_snapshot|{'old_flowering':old_schedule,
                                             'issued_before':issued_before,'issued_after':r.issued}
    audit(s,u,'主管核准换地重播，原批扣种'+str(seeds)+'粒 '+x.code)
    s.flush();return pack(x)
