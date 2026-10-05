"""Read-only prerequisites and public account details; never inspect token files."""
import shutil


def read_account(rpc):
    response = rpc.call('account/read', {'refreshToken': False})
    account = response.get('account')
    if not account or account.get('type') != 'chatgpt':
        raise RuntimeError('ChatGPT sign-in required. Run: codex login')
    return account


def preflight():
    codex = shutil.which('codex')
    if not codex:
        raise RuntimeError('Codex CLI not found on PATH. Install Codex, then run: codex login')
    from banked_reset import RPC
    rpc = RPC(codex)
    try:
        account = read_account(rpc)
        # A cached identity alone does not establish current service access.
        rpc.call('account/rateLimits/read')
        return account
    finally:
        rpc.close()
