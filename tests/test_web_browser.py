"""Local real-SDK loading checks. No meeting server is contacted."""
from pathlib import Path
from meeting_test_runtime.models import ClientProfile
from meeting_test_runtime.web_client import BrowserWorker, WebTestClient


def test_real_sdk_loads_in_isolated_browser_contexts_without_network_login():
    worker=BrowserWorker(headless=True)
    a=b=None
    try:
        profile=ClientProfile("LOCAL_WEB","web",{}, {"ces_addr":"https://example.invalid"})
        a=WebTestClient(profile,worker=worker)
        b=WebTestClient(profile,worker=worker)
        async def inspect():
            await a.page.evaluate("window.__testMarker='A'")
            assert await b.page.evaluate("window.__testMarker ?? null") is None
            assert await a.page.evaluate("typeof window.webTest.invoke") == "function"
            assert await b.page.evaluate("window.webTest.sdkVersion") == "1.8.18"
        worker.submit(inspect())
        assert a.events.mark()==0 and b.events.mark()==0
    finally:
        if a: a.close()
        if b: b.close()
        worker.close()


def test_sdk_right_callbacks_ignore_unrelated_and_late_release_replies():
    """Execute the SDK's actual methods with deliberately interleaved replies."""
    source=(Path(__file__).resolve().parents[1]/'hst-rtc-sdk-1.8.18.es.unmin.js').read_text(encoding='utf-8')
    methods={name:source[source.index('  '+name+'('):source.index('\n  '+following+'(',source.index('  '+name+'('))]
             for name,following in [('applyAuth','applyWhiteBoardAuth'),('applyWhiteBoardAuth','unApplyAuth'),('unApplyAuth','handleLaunchAuth')]}
    worker=BrowserWorker(headless=True)
    async def verify():
        page=await worker.browser.new_page()
        try:
            return await page.evaluate('''methods => {
              for (const [name,body] of Object.entries(methods)) {
                let callback,success=0,failure=0;const sends=[];
                const events={off:()=>{},on:(_,f)=>{callback=f;}};
                const data={proxySocket:{send:s=>sends.push(JSON.parse(s))}};
                const sdk=new Function('events','EVENT_TYPE','data','User$1','__spreadValues','return ({'+body+'})')(
                  events,{APPLY_AUTH:'right'},data,{curUser:{UserID:10}},Object.assign);
                const operation=name==='unApplyAuth'?0:name==='applyAuth'?4:1;
                sdk[name]({type:6,userId:10,operation},()=>success++,()=>failure++);
                const reply=(op,type=6,index=0,result=0)=>callback({result,msg:'actual-error',data:{Operation:op,RightType:type,RightIndex:index,OccupiedUserID:0}});
                reply(0,1);reply(operation,4);reply(operation,6,9);
                if (name!=='unApplyAuth') reply(0,6);
                if(success||failure||sends.length!==1)throw Error(name+' consumed unrelated reply');
                reply(operation);
                if(name==='applyAuth') {
                  if(sends.length!==2||sends[1].Data.Operation!==1)throw Error('query did not request actual right');
                  reply(1);
                }
                if(success!==1||failure!==0)throw Error(name+' lost matching reply');
                // A matching business error must still fail a new request.
                sdk[name]({type:6,userId:10,operation},()=>success++,()=>failure++);
                reply(operation,6,0,8478);
                if(success!==1||failure!==1)throw Error(name+' hid real rejection');
              }
              return true;
            }''',methods)
        finally:await page.close()
    try:assert worker.submit(verify())
    finally:worker.close()


def test_sdk_screen_capture_failure_calls_fail_without_requesting_rights():
    source=(Path(__file__).resolve().parents[1]/'hst-rtc-sdk-1.8.18.es.unmin.js').read_text(encoding='utf-8')
    start=source.index('  async createScreenSharing(success, fail)')
    method=source[start:source.index('\n  async getScreenSource(',start)]
    worker=BrowserWorker(headless=True)
    async def verify():
        page=await worker.browser.new_page()
        try:
            return await page.evaluate('''async body => {
              const user={UserID:10};let rights=0,success=0,error=null;
              const sdk=new Function('data','User$1','config','Auth$1','return ({'+body+'})')(
                {hstRtcEngine:{MediaType:{},getScreenShareStream:async()=>null}},
                {curUser:user,userMap:new Map([[10,user]])},{CLIENT_TYPE:'WEB'},
                {applyAuth:()=>rights++});
              sdk._checkHasCreatRight=()=>true;
              await sdk.createScreenSharing(()=>success++,e=>{error=e;});
              return success===0&&rights===0&&error?.code==='SCREEN_CAPTURE_FAILED';
            }''',method)
        finally:await page.close()
    try:assert worker.submit(verify())
    finally:worker.close()
