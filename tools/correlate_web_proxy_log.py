"""Read-only SSH correlation of actual browser business frames and proxy logs."""
import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict, Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re

import paramiko

# Excludes authentication and chair-password commands, not just their display fields.
BUSINESS = {6504, 6505, 6508, 6509, 6510, 6512, 6513, 6514, 6530, 6542, 6543}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def requests(folder):
    rows = []
    for path in sorted(folder.glob('*-result.json')):
        result = json.loads(path.read_text(encoding='utf-8'))
        for attachment in result.get('attachments', []):
            if attachment['name'] != '全部实际公开方法与发送轨迹':
                continue
            traces = json.loads((folder / attachment['source']).read_text(encoding='utf-8'))
            for alias, values in traces.items():
                for trace in values:
                    if trace.get('trace_type') != 'json_command' or trace.get('cmdid') not in BUSINESS:
                        continue
                    packet = json.loads(trace['raw_json'])
                    rows.append({'case': result['name'], 'alias': alias, 'cmdid': trace['cmdid'],
                                 'sent_at': trace['sent_at'], 'packet': packet})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('results', type=Path)
    parser.add_argument('--host', default='192.168.7.185')
    parser.add_argument('--date', default='2026-10-13')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    password = os.environ.get('WEB_LOG_SSH_PASSWORD')
    if not password:
        raise ValueError('Use temporary WEB_LOG_SSH_PASSWORD; credentials are not stored in this tool/report')
    folder = '/hst/meeting/FMWebProxy/log/' + args.date
    # Freeze local evidence before reading logs; an active test batch can append files.
    captured_requests = requests(args.results)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    parsed = defaultdict(list)
    files = []
    try:
        ssh.connect(args.host, port=22, username='root', password=password, timeout=10)
        sftp = ssh.open_sftp()
        for entry in sorted(sftp.listdir_attr(folder), key=lambda e: e.filename):
            if not entry.filename.startswith('FMWebProxy-') or not entry.filename.endswith('.log'):
                continue
            filename = folder + '/' + entry.filename
            files.append(filename)
            with sftp.open(filename, 'rb') as handle:
                lines = handle.read().decode('utf-8', 'replace').splitlines()
            for number, line in enumerate(lines, 1):
                if 'ProcUserWebMsg' not in line:
                    continue
                clock = re.search(r'\b(\d{2}:\d{2}:\d{2}\.\d+)\b', line)
                payload = re.search(r'\bMsg:\s*(\{.*)', line)
                if not clock or not payload:
                    continue
                try:
                    packet, _ = json.JSONDecoder().raw_decode(payload[1])
                    if packet.get('CmdID') not in BUSINESS:
                        continue
                    timestamp = datetime.strptime(args.date + ' ' + clock[1], '%Y-%m-%d %H:%M:%S.%f').timestamp()
                    parsed[canonical(packet)].append((timestamp, filename, number))
                except (ValueError, TypeError):
                    continue
    finally:
        ssh.close()
    for entries in parsed.values():
        entries.sort()
    evidence = []
    for request in captured_requests:
        entries = parsed.get(canonical(request['packet']), [])
        timestamps = [entry[0] for entry in entries]
        start = bisect_left(timestamps, request['sent_at'] - 1)
        stop = bisect_right(timestamps, request['sent_at'] + 1)
        candidates = entries[start:stop]
        match = min(candidates, key=lambda entry: abs(entry[0] - request['sent_at'])) if candidates else None
        evidence.append({**request, 'matched': bool(match), 'proxy_file': match[1] if match else None,
                         'proxy_line': match[2] if match else None,
                         'time_difference_seconds': round(match[0] - request['sent_at'], 6) if match else None})
    counts = Counter('matched' if row['matched'] else 'unmatched' for row in evidence)
    report = {'scope': 'Second-stage root attachments, real JSON business requests only; excludes login/passwords, FM XML and PaaS',
              'host': args.host, 'date': args.date, 'read_only': True, 'proxy_files': files,
              'counts': dict(counts), 'evidence': evidence}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'requests': len(evidence), 'counts': dict(counts), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
