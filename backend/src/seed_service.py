from sqlalchemy import select,func
from datetime import date,timedelta
from .db import Base,engine,Session
from .models import *
from .auth import hashed

def seed():
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('operator','supervisor','viewer'):
            email=role+'@qinghe.demo'
            if not s.scalar(select(User).where(User.email==email)):s.add(User(email=email,role=role,password_hash=hashed('Seeds2026!')))
        a=s.scalar(select(Accession).where(Accession.code=='QH-021'))
        if not a:a=Accession(code='QH-021',crop='青豆',name='青禾早熟青豆',origin='青禾北圃2023采集',minimum=300);s.add(a);s.flush()
        for code,generation,available in [('QH-021-G3',3,960),('QH-021-POLLEN',3,400)]:
            if not s.scalar(select(Lot).where(Lot.code==code)):s.add(Lot(accession_id=a.id,code=code,generation=generation,available=available,stored_months=36,temperature=4,initial_rate=94))
        other=s.scalar(select(Accession).where(Accession.code=='QH-087'))
        if not other:other=Accession(code='QH-087',crop='青豆',name='异源晚熟青豆',origin='南坡异源对照',minimum=300);s.add(other);s.flush()
        if not s.scalar(select(Lot).where(Lot.code=='QH-087-G2')):s.add(Lot(accession_id=other.id,code='QH-087-G2',generation=2,available=800,stored_months=18,temperature=-8,initial_rate=96))
        corn=s.scalar(select(Accession).where(Accession.code=='QH-055'))
        if not corn:corn=Accession(code='QH-055',crop='黄玉米',name='青禾山地黄玉米',origin='西坡老种自留',minimum=300);s.add(corn);s.flush()
        if not s.scalar(select(Lot).where(Lot.code=='QH-055-G1')):s.add(Lot(accession_id=corn.id,code='QH-055-G1',generation=1,available=700,stored_months=12,temperature=4,initial_rate=92))
        for i in range(18):
            code=chr(65+i//6)+str(i%6+1)
            if not s.scalar(select(Plot).where(Plot.code==code)):s.add(Plot(code=code,row=i//6,position=i%6,capacity=250))
        s.flush();lot=s.scalar(select(Lot).where(Lot.code=='QH-021-G3'))
        if not s.scalar(select(GerminationTest).where(GerminationTest.code=='GT-SEED-80')):s.add(GerminationTest(lot_id=lot.id,code='GT-SEED-80',sampled=100,germinated=80,state='recorded'))
        # 历史下田出苗档案：只补缺，不扣库存；青豆6条可拟合区间，黄玉米2条样本不足→待核。
        # 每条为已收获闭环：复测→复壮→隔离花期(已释放)→授粉(已停用)→出苗登记。
        history=[
            (a,lot,'青豆',[('2024-04-02',120,98),('2024-04-08',120,104),('2024-09-12',110,86),
                          ('2025-04-05',130,112),('2025-04-11',120,100),('2025-09-15',110,81)]),
            (corn,s.get(Lot,s.scalar(select(Lot.id).where(Lot.code=='QH-055-G1'))),'黄玉米',
             [('2025-05-06',100,74),('2025-05-12',100,69)]),
        ]
        plot_ids=[p.id for p in s.scalars(select(Plot).order_by(Plot.id)).all()]
        for accession,src_lot,crop,seasons in history:
            for j,(sowing,sown,emerged) in enumerate(seasons):
                tag=accession.code+'-H'+str(j+1)
                if s.scalar(select(Emergence).where(Emergence.code=='EM-'+tag)):continue
                sowing_day=date.fromisoformat(sowing);end_day=(sowing_day+timedelta(days=5)).isoformat()
                gt=GerminationTest(lot_id=src_lot.id,code='GT-'+tag,sampled=50,germinated=45,state='ready',
                                   prediction={'estimate_pct':90.0,'lower95_pct':86.0,'upper95_pct':94.0,
                                               'observed_pct':90.0,'renewal_required':False,'threshold_pct':85})
                s.add(gt);s.flush()
                r=Regeneration(code='RG-'+tag,test_id=gt.id,lot_id=src_lot.id,issued=sown,state='harvested',
                               reason=crop+'历史下田复壮档案',vitality_snapshot=gt.prediction)
                s.add(r);s.flush()
                plot_id=plot_ids[(r.id+j)%len(plot_ids)]
                s.add(Flowering(regeneration_id=r.id,plot_id=plot_id,start=sowing,end=end_day,state='released'))
                s.add(Pollination(regeneration_id=r.id,mother_lot_id=src_lot.id,father_lot_id=src_lot.id,
                                  bag_code='BAG-'+tag,bagged=True,flowers=max(1,emerged-10),state='pollinated',active=False))
                s.add(Emergence(regeneration_id=r.id,code='EM-'+tag,sown=sown,emerged=emerged,
                                sowing_date=sowing,state='superseded'))
        s.commit();return {cls.__tablename__:s.scalar(select(func.count()).select_from(cls))for cls in (User,Accession,Lot,GerminationTest,Regeneration,Plot,Flowering,Pollination,Harvest,Distribution,Emergence,Resow)}
