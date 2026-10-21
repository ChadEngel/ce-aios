"""MailGraph port + folder/move primitives.

The port is deliberately tiny and duck-typed (no ABC to inherit — adapters
just implement the methods). The Bifrost-MCP adapter lives in the app layer
(next to digest.py); a future direct-Graph adapter or a test double
implements the same six methods. The engine never imports the adapter —
the DEPENDENCY POINTS ONE WAY.

    list_folders() -> {lowercase_name: folder_id}
    list_folder(folder_id, top, skip=None, filter=None, orderby=None) -> [msg]
    create_folder(name) -> None
    move(message_id, folder_id) -> None
    delete(message_id) -> None          # soft delete only (D3)
"""
import time


def folder_map(g):
    return g.list_folders()


def ensure_folder(g, name, log):
    """Idempotent folder get-or-create. Retries the re-list once because the
    MCP/Graph layer has shown eventual visibility on create."""
    fid = g.list_folders().get(name.lower())
    if fid:
        return fid
    g.create_folder(name)
    log(f"file: created folder: {name}")
    time.sleep(1)
    fid = g.list_folders().get(name.lower())
    if not fid:
        raise RuntimeError(f"folder create/list failed for {name}")
    return fid


def move(g, message_id, folder_id):
    g.move(message_id, folder_id)


def delete(g, message_id):
    g.delete(message_id)


def folder_messages(g, folder_id, top, skip=None, filter=None, orderby=None):
    """Pass-through to the port; adapters parse the wire format and return a
    plain list of message dicts (raise on anything else)."""
    return g.list_folder(folder_id, top, skip=skip, filter=filter,
                         orderby=orderby)