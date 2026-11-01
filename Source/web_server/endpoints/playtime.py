import threading

from util import echo_link
from web_server._logic import web_server_handler, server_path


@server_path('/rfd/report-playtime')
def _(self: web_server_handler) -> bool:
    '''
    Called once a minute per player by the RCC startup script. Forwards to the Echo website.
    '''
    if not self.is_privileged:
        self.send_error(403)
        return True

    database = self.server.storage.players
    user_code = database.get_player_field_from_index(
        database.player_field.IDEN_NUM,
        int(self.query['userId']),
        database.player_field.USERCODE,
    )
    seconds = int(self.query['seconds'])
    if echo_link.enabled() and user_code is not None and 0 < seconds <= 120:
        threading.Thread(target=echo_link.report_playtime, args=(user_code, seconds), daemon=True).start()

    self.send_data(b'ok')
    return True
