#!/usr/bin/env python3
"""Negotiate SMB dialects only: no credentials, session login, or file operations.
Optional diagnostic dependency: smbprotocol. Not needed by backup runtime.
"""
from datetime import datetime
import json
import uuid
from zoneinfo import ZoneInfo


def main():
    from smbprotocol.connection import Connection, Dialects
    results = []
    for name, dialect in [('automatic', None), ('2.0.2', Dialects.SMB_2_0_2),
                          ('2.1', Dialects.SMB_2_1_0), ('3.0', Dialects.SMB_3_0_0)]:
        connection = Connection(uuid.uuid4(), '192.0.2.1', 445)
        item = {'offered': name}
        try:
            connection.connect(dialect=dialect, timeout=4)
            item.update(status='negotiated', selected=hex(connection.dialect))
        except Exception as error:
            item.update(status='not-negotiated', error_type=type(error).__name__)
        finally:
            connection.disconnect()
        results.append(item)
    print(json.dumps({'checked_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
                      'authentication_attempted': False, 'file_operations_attempted': False,
                      'results': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
