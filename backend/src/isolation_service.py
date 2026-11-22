from fastapi import HTTPException
from sqlalchemy import select
from .models import Plot,Flowering,Regeneration,Lot,Accession,Pollination
from .common import lock,pack,add,audit

def save(s,d,u,id=None):
    r=lock(s,Regeneration,d.regeneration_id)
    if r.state!='issued':raise HTTPException(409,'隔离排期要求已领种且尚未收获的复壮单')
    plot=s.get(Plot,d.plot_id)
    if not plot:raise HTTPException(404,'隔离小区不存在')
    if r.issued>plot.capacity:raise HTTPException(409,'领种株数超过地块容量')
    x=lock(s,Flowering,id) if id else None
    if x and (x.regeneration_id!=r.id or x.state!='planned'):raise HTTPException(409,'只可修订原未分配花期')
    data=d.model_dump(mode='json')
    if x:
        for k,v in data.items():setattr(x,k,v)
    else:x=Flowering(**data);s.add(x)
    s.flush();audit(s,u,'编制隔离花期 '+r.code);return pack(x)

def assign(s,id,u):
    # Lock all 18 plots in stable ID order to serialize adjacent-plot checks.
    plots=s.scalars(select(Plot).order_by(Plot.id).with_for_update()).all()
    pre=s.get(Flowering,id)
    if not pre:raise HTTPException(404,'花期记录不存在')
    r=lock(s,Regeneration,pre.regeneration_id);x=lock(s,Flowering,id)
    if x.state!='planned' or r.state!='issued':raise HTTPException(409,'仅已领种待分配花期可核准')
    plot=next(p for p in plots if p.id==x.plot_id);lot=s.get(Lot,r.lot_id);crop=s.get(Accession,lot.accession_id).crop
    active=s.scalars(select(Flowering).where(Flowering.state=='assigned')).all()
    for other in active:
        neighbour=next(p for p in plots if p.id==other.plot_id)
        overlap=x.start<=other.end and x.end>=other.start
        near=abs(plot.row-neighbour.row)+abs(plot.position-neighbour.position)<=1
        oldregen=s.get(Regeneration,other.regeneration_id);oldlot=s.get(Lot,oldregen.lot_id);oldcrop=s.get(Accession,oldlot.accession_id).crop
        if overlap and (plot.id==neighbour.id or (near and crop==oldcrop)):raise HTTPException(409,'相邻同作物花期重叠：'+neighbour.code+' '+other.start+'至'+other.end)
    x.state='assigned';audit(s,u,'主管核准隔离花期 '+r.code);return pack(x)

def prepare(s,d,u,id=None):
    r=lock(s,Regeneration,d.regeneration_id)
    f=s.scalar(select(Flowering).where(Flowering.regeneration_id==r.id))
    if r.state!='issued' or not f or f.state!='assigned':raise HTTPException(409,'先核准隔离小区和花期')
    mother=s.get(Lot,d.mother_lot_id);father=s.get(Lot,d.father_lot_id)
    if not mother or not father:raise HTTPException(404,'亲本种批不存在')
    if mother.id!=r.lot_id or mother.accession_id!=father.accession_id or mother.generation!=father.generation:raise HTTPException(409,'母本须为领种批，父本须同种质同代次；复壮不接受异源杂交')
    if d.flowers>r.issued:raise HTTPException(409,'授粉花数不能超过下田株数（含重播补领'+str(r.issued)+'株）')
    x=lock(s,Pollination,id) if id else None
    if x and (x.regeneration_id!=r.id or not x.active or x.state!='prepared'):raise HTTPException(409,'授粉后不可覆盖亲本或袋签；重播后请用新袋签另登记')
    if not x:
        # 每复壮单一条授粉行：重播先成会停用旧行，新配对复用该行但必须换新袋签。
        old=s.scalar(select(Pollination).where(Pollination.regeneration_id==r.id))
        if old:
            if old.active:raise HTTPException(409,'本单已有进行中的授粉袋签，请修订原记录')
            if d.bag_code==old.bag_code:raise HTTPException(409,'旧袋签已停用，重播须启用全新袋签编号')
            old.active=True;old.state='prepared';old.bagged=False;x=old
        else:
            x=Pollination(**d.model_dump());s.add(x);s.flush();audit(s,u,'登记亲本与套袋 '+d.bag_code);return pack(x)
    for k,v in d.model_dump().items():setattr(x,k,v)
    s.flush();audit(s,u,'登记亲本与套袋 '+d.bag_code);return pack(x)

def pollinate(s,id,u):
    pre=s.get(Pollination,id)
    if not pre:raise HTTPException(404,'授粉记录不存在')
    if not pre.active:raise HTTPException(409,'该袋签已随换地重播停用，请在新地块重新套袋')
    # 与prepare保持同一锁序：复壮根记录 → 授粉记录。
    r=lock(s,Regeneration,pre.regeneration_id);x=lock(s,Pollination,id)
    if x.state!='prepared' or r.state!='issued':raise HTTPException(409,'授粉不可重复')
    if not x.bagged:raise HTTPException(409,'先补充授粉袋隔离检查，未套袋不能授粉')
    mother=s.get(Lot,x.mother_lot_id);father=s.get(Lot,x.father_lot_id)
    if mother.id!=r.lot_id or mother.accession_id!=father.accession_id or mother.generation!=father.generation:raise HTTPException(409,'亲本代次或种质不符')
    x.state='pollinated';audit(s,u,'主管确认隔离授粉 '+x.bag_code);return pack(x)
