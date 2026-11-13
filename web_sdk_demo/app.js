import HST from '/sdk.js';

// 每个页面只创建一个HST。不同账号由Python放到独立浏览器上下文。
const sdk = new HST();
const events = [];
const nativeCalls = [];
let joined = false;
let lastBoard = null;
const clone = value => value === undefined ? null : JSON.parse(JSON.stringify(value));
function log(value) {
  events.push(clone(value));
  // 仅显示脱敏字段；不向页面输出登录参数、token或账号密码。
  const view = document.querySelector('#log');
  if (view) view.textContent = JSON.stringify(events.slice(-60), null, 2);
}
sdk.on('all', (name, data) => {
  if(name==='notify' && [2001,2010,2012].includes(Number(data?.code))) joined=false;
  if (['LOGIN_MSG','USER.CHANGE','LAYOUT_CHANGE','CLOUD_RECORD','RECODE_ERROR','Proxy.Status','notify','Manager.Invite.Media'].includes(name)) {
    log({name, at: Date.now(), data});
  }
});
sdk.on('Proxy.Status', () => { joined = false; });
function callbackCall(method, args, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject({origin:'sdk', message:`${method} callback timeout`}), timeoutMs);
    const done = value => { clearTimeout(timer); resolve(clone(value)); };
    const fail = value => {
      clearTimeout(timer);
      const detail=(value instanceof Error || value instanceof DOMException)
        ? {name:value.name,message:value.message,code:value.code} : clone(value);
      reject({origin:'sdk', detail});
    };
    try {
      const pending = sdk[method](...args, done, fail);
      if (pending && typeof pending.catch === 'function') pending.catch(fail);
    } catch (error) { fail({message:error.message}); }
  });
}
function identity() {
  const user = sdk.getLocalUser();
  return {room_user_id:user?.UserID ?? null, room_id:window.webTest.roomId ?? null,
    data_state:user?.DataState ?? null, manager_state:user?.ManagerState ?? null,
    user:clone(user), joined};
}
function deviceId(params, kind, user) {
  if (params.dev_id !== undefined) return params.dev_id;
  if (params.device_id !== undefined) return params.device_id;
  const target = user ?? sdk.getLocalUser();
  if (kind === 'Audio') {
    if (!target?.Audio) throw new Error('目标没有已上报音频设备');
    return target.Audio.ID;
  }
  const videos = Array.isArray(target?.Video) ? target.Video : target?.Video ? [target.Video] : [];
  const video = videos.find(v => Number(v.ID) !== 5);
  if (!video) throw new Error('目标没有已上报摄像头');
  return video.ID;
}
function targetUser(params) {
  const uid = params.user_id ?? params.dst_user_id ?? sdk.getLocalUser()?.UserID;
  const user = sdk.getUserInfo(Number(uid)) ?? sdk.getUserInfo(uid);
  if (!user) {
    const error=new Error(`目标不在当前成员缓存: ${uid}`);
    error.origin='adapter';error.code='TARGET_NOT_IN_MEETING';
    throw error;
  }
  return user;
}
const handlers = {
  async init(params) {
    document.querySelector('#ces').value=params.ces_addr;
    return callbackCall('init', [{cesAddr:params.ces_addr, platformType:'private',clientType:'WEB'}], params.timeout_ms ?? 30000);
  },
  async login(params) {
    window.webTest.authToken=null;
    window.webTest.authenticatedProfile=null;
    const response = await sdk.accountAuth({userName:params.username,password:params.password,needLoginInfo:true});
    window.webTest.authToken = response.access_token;
    const {access_token,...profile}=response;
    window.webTest.authenticatedProfile=clone(profile);
    return {authenticated:true,profile}; // 本地结果及实际账户查询，不伪造协议Result。
  },
  async join(params) {
    window.webTest.roomId = Number(params.room_id);
    const result = await sdk.accountJoinMeet({roomId:Number(params.room_id),userName:params.username,
      token:window.webTest.authToken,password:params.room_password,forcedLogin:false});
    joined = result.result === 0;
    return clone(result);
  },
  async join_guest(params) {
    window.webTest.roomId=Number(params.room_id);
    window.webTest.authToken=null;
    window.webTest.authenticatedProfile=null;
    const result=await sdk.anonymousJoinMeet({roomId:Number(params.room_id),
      userName:params.display_name,password:params.room_password,forcedLogin:false});
    joined=result.result===0;
    return clone(result);
  },
  get_identity: () => identity(),
  get_auth_state: () => ({has_auth_token:!!window.webTest.authToken,authenticated:!!window.webTest.authenticatedProfile}),
  get_devices: () => sdk.getMediaDevices(),
  get_users: () => Array.from(sdk.getUsers().values()).map(clone),
  get_user_info: params => clone(targetUser(params)),
  get_authenticated_profile: () => clone(window.webTest.authenticatedProfile),
  get_capabilities: () => ({service:clone(sdk.getServiceAuth()), roles:clone(sdk.getRoleAuth())}),
  get_effective_permissions: () => {
    const user=sdk.getLocalUser(), roles=sdk.getRoleAuth();
    const key=user.ManagerState===2?'admin':user.DataState===2?'presenter':user.Role;
    return clone({...roles[key],...user.UserPermission});
  },
  mic_on: params => sdk.broadcastAudio({userId:sdk.getLocalUser().UserID,mediaId:deviceId(params,'Audio')}),
  mic_off: params => sdk.unbroadcastAudio({userId:sdk.getLocalUser().UserID,mediaId:deviceId(params,'Audio')}),
  camera_on: params => sdk.broadcastVideo({userId:sdk.getLocalUser().UserID,mediaId:deviceId(params,'Video')}),
  camera_off: params => sdk.unbroadcastVideo({userId:sdk.getLocalUser().UserID,mediaId:deviceId(params,'Video')}),
  chat: params => {
    if (params.dst_user_id && Number(params.dst_user_id)!==0) throw new Error('Web产品不支持文字私聊');
    if (params.font!==undefined || params.color!==undefined) throw new Error('Web公开方法不支持可变Font/Color');
    return new Promise((resolve,reject)=>sdk.sendMessage(params.text,()=>resolve(null),e=>reject({origin:'sdk',detail:e}),0));
  },
  request_presenter: () => {
    const user=sdk.getLocalUser();
    // SDK自身把未提供DataState视为NONE；不能把undefined转NaN而误拒绝。
    if (Number(user.DataState ?? 0)!==0) throw new Error('主持申请前置必须为DataState=0');
    return sdk.applyModeratorRight();
  },
  release_presenter: () => sdk.modifyUserInfo({UserID:sdk.getLocalUser().UserID,DataState:0}),
  request_manager: params => callbackCall('applyManagerRight',[params.password]),
  release_manager: () => callbackCall('giveUpManagerRight',[]),
  request_remote_av: params => {
    const user=targetUser(params), kind=params.kind==='video'?'Video':'Audio';
    sdk.boradOthersMedia({userId:user.UserID,mediaId:deviceId(params,kind,user)},kind);
    return null;
  },
  respond_remote_av: params => {
    const kind=params.kind==='video'?'Video':'Audio';
    return params.accept ? sdk.agreeOpenMedia(kind) : sdk.refuseOpenMedia(kind);
  },
  close_remote_av: params => {
    const user=targetUser(params), kind=params.kind==='video'?'Video':'Audio';
    return kind==='Audio' ? sdk.unbroadcastAudio({userId:user.UserID,mediaId:deviceId(params,kind,user)})
      : sdk.unbroadcastVideo({userId:user.UserID,mediaId:deviceId(params,kind,user)});
  },
  approve_self_av: params => {
    const user=targetUser(params), kind=params.kind==='video'?'Video':'Audio';
    return sdk.modifyUserInfo({UserID:user.UserID,[kind]:{ID:deviceId(params,kind,user),State:2}});
  },
  start_record: () => sdk.startRecord(),
  stop_record: () => sdk.stopRecord(),
  start_screen_share: () => callbackCall('createScreenSharing',[],20000),
  async stop_screen_share() {
    await callbackCall('closeScreenSharing',[sdk.getLocalUser().UserID]);
    sdk.stopBrowserScreenSharing(sdk.getLocalUser().UserID);
    return null;
  },
  start_whiteboard: params => new Promise((resolve,reject)=> {
    sdk.operateShare({type:'create',data:{board_name:params.name??'E2E白板',width:1280,height:768,
      page:1,broadcastOwnLayout:true},callback:value=> {
        if(value?.msg!=='success') {reject({origin:'sdk',detail:value});return;}
        lastBoard=value.data;
        resolve(clone(value.data));
      }});
  }),
  stop_whiteboard: params => {
    if (!lastBoard && !params.media_id) throw new Error('没有本次白板资源');
    const data={...lastBoard,mediaId:params.media_id??lastBoard.mediaId,
      userId:sdk.getLocalUser().UserID,mediaType:'WHITE_BOARD'};
    // 无渲染Demo也要完成应用层的共享资源移除；使用SDK公开维护接口。
    // 先移除布局和释放本白板权限，再关闭真实资源，防止下次创建携带旧白板。
    sdk.cacheShareMedias({media:data,type:'dispose'});
    sdk.operateShare({type:'close',mine:true,data});
    // 此公开分支不调用callback。真实19003由Python消息缓存验收，不伪造Response。
    lastBoard=null;
    return null;
  },
  async leave() { const result=await sdk.exitMeet(); joined=false; return clone(result); },
  async close() {
    if (joined) await handlers.leave();
    await sdk.uninit();
    joined=false;
    return null;
  }
};
// 只包装实例的公开门面用于取证，保持原调用和返回行为，不修改SDK文件/私有发送。
for (const name of ['init','uninit','accountAuth','accountJoinMeet','anonymousJoinMeet','getLocalUser','getUserInfo','getUsers',
  'getMediaDevices','getServiceAuth','getRoleAuth','broadcastAudio','unbroadcastAudio','broadcastVideo','unbroadcastVideo',
  'sendMessage','applyModeratorRight','applyManagerRight','giveUpManagerRight','modifyUserInfo','boradOthersMedia',
  'agreeOpenMedia','refuseOpenMedia','startRecord','stopRecord','createScreenSharing','closeScreenSharing',
  'stopBrowserScreenSharing','operateShare','cacheShareMedias','exitMeet']) {
  const original=sdk[name];
  sdk[name]=(...args)=> {
    nativeCalls.push({method:name,args:args.map(arg=>typeof arg==='function'?'[callback]':clone(arg)),
      called_at:Date.now()/1000});
    return original.apply(sdk,args);
  };
}
window.webTest = {
  sdkVersion:'1.8.18',authToken:null,roomId:null,
  async invoke(operation, params={}) {
    if (!Object.hasOwn(handlers,operation)) throw new Error(`不支持的Web业务操作: ${operation}`);
    const result=await handlers[operation](params);
    return clone(result);
  },
  identity,
  nativeMark: () => nativeCalls.length,
  nativeSnapshot: after => clone(nativeCalls.slice(after)),
  events: () => clone(events)
};
document.querySelector('#join').onclick=async()=> {
  try {
    await handlers.init({ces_addr:document.querySelector('#ces').value});
    const params={username:document.querySelector('#username').value,password:document.querySelector('#password').value};
    if(document.querySelector('#identity-type').value==='guest') {
      await handlers.join_guest({display_name:params.username,room_id:document.querySelector('#room').value});
    } else {
      await handlers.login(params);
      await handlers.join({username:params.username,room_id:document.querySelector('#room').value});
    }
    log({operation:'join',result:identity()});
  } catch(error) {log({error:error.message??error});}
};
document.querySelector('#invoke').onclick=async()=> {
  const operation=document.querySelector('#operation').value;
  try {log({operation,result:await window.webTest.invoke(operation,JSON.parse(document.querySelector('#params').value))});}
  catch(error){log({operation,error:error.message??error});}
};
document.querySelector('#capture-trigger').onclick=()=> {
  const request=window.webTest.captureRequest;
  if (request) window.webTest.capturePromise=window.webTest.invoke(request.operation,request.params);
};
