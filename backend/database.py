from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

import config

DATABASE_URL = config.DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    # 对已存在的 forecasts 表补建索引 (create_all 不会改老表结构).
    # checkfirst=True 保证已存在索引时不报错.
    from models import Forecast
    Forecast.__table_args__[0].create(engine, checkfirst=True)
