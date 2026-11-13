"""将业务输入映射为下行字段；不从实际响应反推预期，不构造协议。"""


def flatten(value, prefix=''):
    result = {}
    if isinstance(value, dict):
        for key, item in value.items(): result.update(flatten(item, f'{prefix}.{key}' if prefix else key))
    elif isinstance(value, list):
        for i, item in enumerate(value): result.update(flatten(item, f'{prefix}.{i}'))
    elif value is not None:
        result[prefix] = value
    return result


def notify_fields(operation, params, actor='operator'):
    uid = params.get('user_id', '${' + actor + '.room_user_id}')
    if operation in ('set_layout','set_window_state'):
        fields={'Mode':params.get('mode',3 if operation=='set_layout' else 1)}
        for key,wire in [('layout_type','VideoRelayID'),('video_relay_id','VideoRelayID'),
                         ('full','Full'),('data_active','DataActive'),
                         ('full_video_id','FullVideoID'),
                         ('full_video_media_id','FullVideoMediaID'),
                         ('valid_full_mode','ValidFullMode')]:
            if params.get(key) is not None:fields[wire]=params[key]
        videos=params.get('videos')
        if videos is None and params.get('user_ids'):
            videos=[{'UserID':user,'MediaID':0,'Pos':i} for i,user in enumerate(params['user_ids'])]
        if videos:fields.update(flatten(videos,'Video'))
        return fields
    if operation == 'chat':
        fields={'SrcUserID':'${'+actor+'.room_user_id}',
                'DstUserID':params.get('dst_user_id',0),
                'Text':params.get('text','')}
        if params.get('color') is not None: fields['Color']=params['color']
        for key,value in (params.get('font') or {}).items():
            if value is not None: fields[f'Font.{key}']=value
        return fields
    if operation in ('mic_on','mic_off','mic_request','camera_on','camera_off','set_audio_state','set_video_state','set_user_audio','set_user_video'):
        audio = operation.startswith('mic_') or 'audio' in operation
        kind = 'Audio' if audio else 'Video'
        state = params.get('state', {'mic_on':2,'mic_off':0,'mic_request':1,'camera_on':2,'camera_off':0}.get(operation))
        if not audio and state == 1: state = 2
        return {'UserID': uid, f'{kind}.ID':params.get('dev_id', 0 if audio else 1), f'{kind}.State':state}
    if operation == 'set_role':
        field = {'admin':'ManagerState','presenter':'DataState','chairman':'Right'}[params['role']]
        value = (3 if params['enabled'] else 2) if field == 'Right' else (2 if params['enabled'] else 0)
        return {'UserID': uid, field:value}
    if operation in ('set_user_manager','set_user_presenter'):
        return {'UserID':params['user_id'], 'ManagerState' if operation=='set_user_manager' else 'DataState':params['state']}
    if operation in ('start_record','pause_record','stop_record','operate_record'):
        return {'ServerRecordStatus':params.get('state', {'start_record':2,'pause_record':1,'stop_record':0}.get(operation))}
    if operation in ('lock_room','unlock_room','lock_meeting'):
        return {'Lock':int(bool(params.get('lock',True))) if operation=='lock_meeting' else (1 if operation=='lock_room' else 0)}
    if operation == 'set_subtitle': return {'Duration':params.get('duration',0), 'Data':params['data']}
    if operation == 'send_subtitles':
        return {f'Data.{wire}':params[source] for source,wire in (
            ('content','Content'),('font_size','FontSize'),('font_color','FontColor'),
            ('subtitle_type','Type'),('align','Align')) if source in params}
    if operation == 'set_layout_http':
        layout = params['layout']
        return {wire:layout[key] for key,wire in [('mode','Mode'),('full','Full'),('dataActive','DataActive'),('videoRelayId','VideoRelayID')] if key in layout}
    if operation == 'send_file': return {'SrcUserID':'${'+actor+'.room_user_id}', **flatten(params['file_info'],'FileInfo')}
    if operation == 'set_room_data': return flatten(params['items'],'DataItem')
    if operation == 'send_sysmsg': return {'ID':params['sys_msg_id']}
    if operation == 'conf_logout': return {'UserID':'${'+actor+'.room_user_id}'}
    if operation == 'bring_to_main': return {'SrcUserID':'${'+actor+'.room_user_id}'}
    if operation in ('kick_user','remove_user'):
        return {'UserID':params['user_id'], 'KickID': -2 if operation=='remove_user' else '${'+actor+'.room_user_id}'}
    if operation in ('close_room','close_meeting'):
        return {'Reason':params.get('reason',0)}
    if operation in ('start_aux_stream','stop_aux_stream'):
        vnc=params.get('kind','media')=='vnc'
        fields={'UserID':'${'+actor+'.room_user_id}', 'VNCState' if vnc else 'MediaShareState':2 if operation=='start_aux_stream' else 0}
        return fields
    return {}


def response_fields(operation, params, actor='operator'):
    if operation in ('request_right','release_right'):
        return {'Result':0,'Operation':1 if operation=='request_right' else 0,
                'RightType':params.get('right_type',1),
                'OccupiedUserID':'${'+actor+'.room_user_id}' if operation=='request_right' else 0}
    if operation in ('front_authenticate','front_login','front_login_guest','front_login_by_invite_code','conf_login'):
        return {'Result':0}
    if operation=='conf_query_call_list':return {'OprResult':0,'CallType':params.get('call_type',2)}
    if operation=='create_group':
        fields={}
        for i,room in enumerate(params['rooms']):
            for key in ('Theme','State','MaxUserCount','EnableJoin','EnableLeave'):
                if key in room: fields[f'RoomInfo.{i}.{key}']=room[key]
        return fields
    if operation=='rename_meeting': return {'Name':params['name']}
    return {}


def validate_response_shape(operation, response):
    required={
        'entrance_get_config':('PlatformType','ConfigCenterAddr','Version'),
        'front_authenticate':('Result','LocalNodeID','LocalUserID'),
        'front_login':('Result','RoomNodeID','RoomID','RoomUserID','RoomSrvNodeID','Token','Server'),
        'front_login_guest':('Result','RoomNodeID','RoomID','RoomUserID','RoomSrvNodeID','Token','Server'),
        'front_login_by_invite_code':('Result','RoomNodeID','RoomID','RoomUserID','RoomSrvNodeID','Token','Server'),
        'conf_login':('Result','RoomInfo','UserInfo','AVServer'),
        'front_get_auth_type':('AuthType',),
        'conf_get_contacts':('depts','local_user','offline_users'),
        'contacts_snapshot':('depts','local_user','offline'),
        'conf_get_room_config':('InviteCode',),
    }.get(operation)
    if required:
        assert isinstance(response,dict), f'{operation}响应不是对象'
        assert all(k in response for k in required), f'{operation}缺少必需响应字段'
    if operation=='front_authenticate':
        assert int(response['LocalUserID'])>0 and response['LocalNodeID'], '认证未返回有效用户或节点'
    if operation in ('front_login','front_login_guest','front_login_by_invite_code'):
        assert int(response['RoomUserID'])>0 and response['Token'] and response['Server'], '入会路由或身份缺失'
    if operation=='conf_login':
        assert isinstance(response['RoomInfo'],dict) and 'Name' in response['RoomInfo']
        assert isinstance(response['UserInfo'],dict) and 'Right' in response['UserInfo']
    if operation=='conf_get_room_config':
        assert str(response['InviteCode']).isdigit() and len(str(response['InviteCode']))==9
