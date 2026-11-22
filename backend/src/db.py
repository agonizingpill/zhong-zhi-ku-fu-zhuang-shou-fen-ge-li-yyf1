import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase,sessionmaker
_url=os.environ['DATABASE_URL']
# MySQL8 生产口径为 READ COMMITTED；SQLite 仅用于本地离线测试，不支持该级别，用其默认隔离。
engine=create_engine(_url,pool_pre_ping=True,**({} if _url.startswith('sqlite') else {'isolation_level':'READ COMMITTED'}))
Session=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase):pass
def session():
    with Session() as s:yield s
