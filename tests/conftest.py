from __future__ import annotations

import sqlite3

import pytest

from src import db as dbm
from src.config import Config, DEFAULTS
import copy


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    c = dbm.connect(tmp_path / "test.db")
    dbm.init_db(c)
    return c


@pytest.fixture
def cfg(tmp_path) -> Config:
    data = copy.deepcopy(DEFAULTS)
    return Config(data, root=tmp_path)
