"""Connect a signed-in Streamlit session to its isolated, shared team workspace."""
import os
from pathlib import Path

import streamlit as st

from app_info import is_public_host
from session import Session
from stats_db import default_path
from team_accounts import AccountError, TeamStore


def storage_folder():
    configured = os.environ.get("R6_TEAM_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if is_public_host():
        return None  # Community Cloud's local disk is disposable; never promise durable accounts there.
    return default_path().parent / "team_accounts"


def store():
    folder = storage_folder()
    if folder is None:
        raise AccountError("Team login needs persistent server storage. Run the free local team server or configure R6_TEAM_DATA_DIR on a host with a persistent volume.")
    return TeamStore(folder)


def current_team():
    token = Session().team_token
    if not token:
        return None
    # Fail closed: never silently fall back to another workspace for an expired session.
    return store().account(token)


def team_folder():
    return store().team_folder(Session().team_token) if current_team() else None


def switch_account(token=None):
    # No replay cache, theme, player choice or planning data crosses an account boundary.
    session = Session()
    session.clear()
    st.query_params.clear()
    if token:
        session.team_token = token


def selected_player():
    session = Session()
    if not session.team_token or session.demo:
        return None
    account = current_team()
    chosen = session.team_person
    return next((p["player"] or None for p in account["directory"] if p["id"] == chosen), None)


def read_document(name):
    session = Session()
    value, revision = store().document(session.team_token, name)
    session.setdefault('team_revision_' + name, revision)
    return value


def write_document(name, value):
    session, key = Session(), 'team_revision_' + name
    if not session.has(key):
        read_document(name)
    store().save_document(session.team_token, name, value, session.get(key))
    session.set(key, session.get(key) + 1)


def shared_active():
    return not Session().demo and bool(current_team())
