const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const http=require('node:http');
const https=require('node:https');
const {spawn}=require('node:child_process');
const {EventEmitter}=require('node:events');
const {randomBytes,createHash,timingSafeEqual}=require('node:crypto');
const {pipeline}=require('node:stream/promises');
const {egressServer}=require('./opencode-egress.cjs');

const canonicalPath=raw=>process.platform==='win32'?fs.realpathSync.native(raw):fs.realpathSync(raw);
const VERSION='2.0.27';
const ARCHIVE='https://registry.npmjs.org/@opencode/cli-linux-x64-baseline/-/cli-linux-x64-baseline-2.0.27.tgz';
const INTEGRITY='5q+5k6G8n4FGme5wf1zQQTX5CDEgakFPkCD45TLGlbecKCpCewJIZNoDR8n2O0HaEewRlyGIWJaNIRFrBQB5YA==';
const WINDOWS_ARCHIVE='https://registry.npmjs.org/@opencode/cli-windows-x64-baseline/-/cli-windows-x64-baseline-2.0.27.tgz';
const WINDOWS_INTEGRITY='7ecPhOMY7nJ7eadGd1I3PSwARonNMQ3L62x5QcIrSecFjE1sFUb5qTu4JnQnPqoiDv0Ag7aJP14RU1nP2zMdbA==';
function projectPath(raw,root){
  const value=canonicalPath(raw),home=os.homedir();
  if(process.platform==='win32'&&[process.env.SystemRoot,process.env.ProgramFiles,process.env.ProgramData,process.env.APPDATA,process.env.LOCALAPPDATA].filter(Boolean).some(root=>value.toLowerCase()===root.toLowerCase()||value.toLowerCase().startsWith(root.toLowerCase()+path.sep)))throw Error('Выбери отдельную папку проекта.');
  if(!fs.statSync(value).isDirectory()||[path.parse(value).root,home,'/home','/tmp','/var','/run','/usr','/etc','/opt'].includes(value)
    ||['/etc','/var','/usr','/boot','/proc','/sys','/dev','/run','/root','/bin','/lib','/lib64','/sbin'].some(p=>value.startsWith(p+path.sep))
    ||[root,...['.ssh','.config','.codex','.claude','.igorek-agent-factory','.gnupg','.aws','.docker','.kube','.local/share/opencode'].map(p=>path.join(home,p))].some(p=>value===p||value.startsWith(p+path.sep)||p.startsWith(value+path.sep)))throw Error('Выбери отдельную папку проекта.');
  return value;
}
function sandboxArgv({root,bridge,projects,script,hostServices=null,profiles=null,binaryRoot=null}){
  const argv=['--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL','--clearenv',
    '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/sbin','/sbin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64',
    '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc','--ro-bind','/etc/ssl/certs','/etc/ssl/certs',
    '--ro-bind',binaryRoot||path.join(root,'bin'),'/opt/opencode','--ro-bind',script,'/opt/igorek/opencode-sandbox.py',
    '--bind',path.join(root,'home'),'/home/opencode','--bind',bridge,'/run/igorek',
    '--setenv','HOME','/home/opencode','--setenv','PATH','/opt/opencode:/usr/bin:/bin','--setenv','LANG','C.UTF-8',
    '--setenv','XDG_CONFIG_HOME','/home/opencode/.config','--setenv','XDG_DATA_HOME','/home/opencode/.local/share',
    '--setenv','XDG_CACHE_HOME','/home/opencode/.cache'];
  for(const entry of ['/etc/ld.so.cache','/etc/fonts','/etc/localtime'])if(fs.existsSync(entry))argv.push('--ro-bind',entry,entry);
  for(const project of projects)argv.push('--bind',project,project);
  if(hostServices){
    argv.push('--ro-bind',hostServices.socket,'/run/igorek-host-services.sock',
      '--ro-bind',path.join(bridge,'host-services-token'),'/run/igorek-host-services-token',
      '--ro-bind',path.join(__dirname,'opencode-host-services-client.py'),'/opt/igorek/host-services-client.py',
      '--setenv','OPENCODE_CONFIG_CONTENT',JSON.stringify({mcp:{servers:{igorek:{type:'local',command:['/usr/bin/python3','-I','/opt/igorek/host-services-client.py'],codemode:false,timeout:{startup:30000,catalog:30000,execution:100000}}}}}));
  }
  if(profiles)argv.push('--ro-bind',profiles.socket,'/run/igorek-profiles.sock');
  argv.push('--chdir',projects[0],'--','/usr/bin/python3','-I','/opt/igorek/opencode-sandbox.py');
  return argv;
}
function run(command,args,options={}){
  return new Promise((resolve,reject)=>{
    const child=spawn(command,args,{stdio:['ignore','ignore','ignore'],shell:false,...options});
    child.once('error',()=>reject(Error('Не удалось выполнить подготовку OpenCode.')));
    child.once('exit',code=>code===0?resolve():reject(Error('Не удалось подготовить OpenCode.')));
  });
}
class OpenCodeService extends EventEmitter{
  constructor(root,{python='/usr/bin/python3',script=path.join(__dirname,'opencode-sandbox.py'),bundle=path.join(__dirname,'runtime'),hostServicesControl=null,profilesControl=null,binaryRoot=null,ownedWorkspace=null,assignedProject=null,diagnostics=false}={}){
    super();this.root=root;this.python=python;this.script=script;this.bundle=bundle;this.state='stopped';this.error='';this.child=null;this.starting=null;this.generation=0;
    this.hostServicesControl=hostServicesControl;this.hostServicesGrant=null;this.profilesControl=profilesControl;this.profilesGrant=null;
    this.binaryRoot=binaryRoot||path.join(root,'bin');
    this.ownedWorkspace=ownedWorkspace;this.assignedProject=assignedProject;this.diagnostics=diagnostics===true;
    fs.mkdirSync(root,{recursive:true,mode:0o700});fs.chmodSync(root,0o700);
    this.file=path.join(root,'connection.json');
    this.config={mode:'sandbox',projects:[],port:0,enabled:true};
    try{
      if(fs.existsSync(this.file)){
        const value=JSON.parse(fs.readFileSync(this.file,'utf8'));
        if(!['sandbox','kvm'].includes(value.mode)||!Array.isArray(value.projects)||value.projects.length>16||value.projects.some(p=>typeof p!=='string')
          ||!Number.isInteger(value.port)||value.port<0||value.port>65535||typeof value.enabled!=='boolean'
          ||(value.host_services!==undefined&&(!Array.isArray(value.host_services)||value.host_services.length>8||value.host_services.some(name=>typeof name!=='string'||!/^[a-z][a-z0-9_-]{0,39}$/.test(name)))))throw Error('invalid');
        this.config=value;
      }
    }catch{this.state='error';this.error='Не удалось прочитать настройки OpenCode. Выбери проект заново.';}
    this.token=randomBytes(32).toString('hex');
    const key=path.join(root,'server-key');
    this.password=fs.existsSync(key)?fs.readFileSync(key,'utf8'):randomBytes(32).toString('base64url');
    fs.writeFileSync(key,this.password,{mode:0o600});fs.chmodSync(key,0o600);
  }
  save(){fs.writeFileSync(this.file+'.tmp',JSON.stringify(this.config),{mode:0o600});fs.renameSync(this.file+'.tmp',this.file);}
  status(){return {state:this.state,error:this.error,installed:fs.existsSync(path.join(this.binaryRoot,process.platform==='win32'?'opencode.exe':'opencode')),version:VERSION,mode:this.config.mode,enabled:this.config.enabled,projects:this.config.projects.slice(),host_services:(this.config.host_services||[]).slice()};}
  transportOptions(){return this.windowsPort?{host:'127.0.0.1',port:this.windowsPort}:{socketPath:path.join(this.bridge,'api.sock')};}
  publish(){this.emit('state',this.status());}
  async install(){
    if(!['linux','win32'].includes(process.platform)||process.arch!=='x64')throw Error('Эта сборка поддерживает Linux и Windows x64.');
    if(this.status().installed)return this.status();
    if(this.installing)return this.installing;
    this.installing=(async()=>{
      this.state='installing';this.error='';this.publish();
      const archive=path.join(this.root,'download.tgz');
      try{
        await new Promise((resolve,reject)=>{
          const request=https.get(process.platform==='win32'?WINDOWS_ARCHIVE:ARCHIVE,response=>{
            if(response.statusCode!==200){response.resume();reject(Error('Не удалось скачать официальный OpenCode.'));return;}
            let size=0;response.on('data',chunk=>{size+=chunk.length;if(size>300*1024*1024)response.destroy(Error('size'));});
            pipeline(response,fs.createWriteStream(archive,{mode:0o600})).then(resolve,reject);
          });request.setTimeout(60000,()=>request.destroy(Error('timeout')));request.on('error',reject);
        });
        const hash=createHash('sha512');for await(const chunk of fs.createReadStream(archive))hash.update(chunk);
        if(hash.digest('base64')!==(process.platform==='win32'?WINDOWS_INTEGRITY:INTEGRITY))throw Error('Контрольная сумма OpenCode не совпала.');
        if(process.platform==='win32')await run(this.python,['-I',path.join(__dirname,'opencode-windows.py'),'extract',archive,path.join(this.root,'stage')]);
        else{
          fs.mkdirSync(path.join(this.root,'stage'),{mode:0o700});
          await run('/usr/bin/tar',['-xzf',archive,'--directory',path.join(this.root,'stage'),'--strip-components=2','package/bin/opencode']);
          fs.chmodSync(path.join(this.root,'stage/opencode'),0o500);
        }
        fs.renameSync(path.join(this.root,'stage'),path.join(this.root,'bin'));
        this.state='stopped';return this.status();
      }catch(error){this.state='error';this.error='Не удалось установить официальный OpenCode 2.0.';throw error;}
      finally{fs.rmSync(archive,{force:true});fs.rmSync(path.join(this.root,'stage'),{recursive:true,force:true});this.publish();}
    })().finally(()=>{this.installing=null;});return this.installing;
  }
  resolveProject(raw){
    const real=canonicalPath(raw);
    if(this.assignedProject&&real!==canonicalPath(this.assignedProject))throw Error('opencode_project_not_granted');
    if(this.ownedWorkspace&&real===canonicalPath(this.ownedWorkspace)&&fs.statSync(real).isDirectory())return real;
    if(this.ownedWorkspace)throw Error('Недопустимая рабочая копия поручения.');
    return projectPath(raw,this.root);
  }
  async addProject(raw){const project=this.resolveProject(raw);if(!this.config.projects.includes(project)){await this.stop();this.config.projects.push(project);}this.config.enabled=true;this.save();return this.status();}
  async configureKvm(file){
    const info=fs.lstatSync(file);
    if(!info.isFile()||info.uid!==process.getuid()||(info.mode&0o077)||info.size>65536)throw Error('Нужен приватный JSON подключения KVM (права 0600).');
    let value;try{value=JSON.parse(fs.readFileSync(file,'utf8'));}catch{throw Error('Некорректный JSON подключения KVM.');}
    if(Object.keys(value).sort().join(',')!=='password,projects,vm'||!/^[-a-f0-9]{36}$/.test(value.vm)||typeof value.password!=='string'||value.password.length<24||value.password.length>256||!Array.isArray(value.projects)||value.projects.length<1||value.projects.length>16)throw Error('Некорректное подключение KVM.');
    const projects=value.projects.map(raw=>projectPath(raw,this.root));
    await this.stop();this.config={...this.config,mode:'kvm',vm:value.vm,projects,enabled:true};this.password=value.password;
    fs.writeFileSync(path.join(this.root,'server-key'),this.password,{mode:0o600});this.save();return this.status();
  }
  async useSandbox(){await this.stop();this.config.mode='sandbox';delete this.config.vm;this.config.enabled=true;this.save();return this.status();}
  async configureHostServices(names){
    if(!Array.isArray(names)||names.length>8||names.some(name=>typeof name!=='string'||!/^[a-z][a-z0-9_-]{0,39}$/.test(name)))throw Error('Некорректный список сервисов.');
    await this.stop();this.config.host_services=[...new Set(names)];this.save();return this.status();
  }
  async start(){
    if(this.child&&this.state==='ready')return this.origin;
    if(this.starting)return this.starting;
    this.starting=this.launch().finally(()=>{this.starting=null;});return this.starting;
  }
  async launch(){
    if(!this.config.enabled)throw Error('Включи интеграцию OpenCode.');
    if(!this.config.projects.length)throw Error('Сначала выбери папку проекта.');
    this.config.projects=this.config.projects.map(raw=>this.resolveProject(raw));
    if(this.config.mode==='sandbox'&&!this.status().installed)throw Error('Сначала установи OpenCode 2.0.');
    await this.stop();const generation=this.generation;this.state='starting';this.error='';this.publish();
    fs.mkdirSync(path.join(this.root,'home'),{mode:0o700,recursive:true});
    this.bridge=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-opencode-'));fs.chmodSync(this.bridge,0o700);
    try{
      if(this.config.mode==='sandbox'&&process.platform!=='win32'){
        this.egress=egressServer();await new Promise((resolve,reject)=>{this.egress.once('error',reject);this.egress.listen(path.join(this.bridge,'egress.sock'),resolve);});
      }
      const sandbox=this.config.mode==='sandbox';
      if(sandbox&&this.profilesControl)this.profilesGrant=await this.profilesControl('grant',{});
      if(this.config.host_services?.length){
        if(!sandbox||!this.hostServicesControl)throw Error('Для локальных сервисов нужен sandbox и подключённый runtime Игорька.');
        const grant=await this.hostServicesControl('grant',{services:this.config.host_services,projects:this.config.projects});
        if(!grant||typeof grant.socket!=='string'||typeof grant.token!=='string'||!/^[a-f0-9]{64}$/.test(grant.token))throw Error('Некорректное разрешение локальных сервисов.');
        this.hostServicesGrant=grant;
        fs.writeFileSync(path.join(this.bridge,'host-services-token'),grant.token,{mode:0o600});
        if(generation!==this.generation)throw Error('Подключение отменено.');
      }
      const windows=process.platform==='win32';
      if(windows&&(!sandbox||this.hostServicesGrant))throw Error('Этот режим OpenCode пока недоступен в Windows.');
      const child=spawn(windows?this.python:sandbox?'/usr/bin/bwrap':this.python,windows?['-I','-X','utf8',path.join(__dirname,'opencode-windows.py')]:sandbox?sandboxArgv({root:this.root,bridge:this.bridge,projects:this.config.projects,script:this.script,hostServices:this.hostServicesGrant,profiles:this.profilesGrant,binaryRoot:this.binaryRoot}):['-I',this.script],
        {stdio:['pipe','pipe','ignore'],shell:false,windowsHide:true,env:windows?{SystemRoot:process.env.SystemRoot,PATH:process.env.PATH,TEMP:process.env.TEMP}:{PATH:'/usr/bin:/bin',LANG:'C.UTF-8'},cwd:this.root});
      this.child=child;child.stdin.on('error',()=>{});
      const gone=()=>{if(this.child!==child)return;this.child=null;this.state='error';this.error='Сервис OpenCode остановился. Нажми «Подключиться». ';this.closeNetwork();this.publish();};
      child.once('exit',gone);
      await new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Sandbox OpenCode не запустился вовремя.')),windows?60000:15000);let pending='';
        const finish=error=>{clearTimeout(timer);error?reject(error):resolve();};
        child.once('error',()=>finish(Error('Не удалось запустить изоляцию OpenCode.')));child.once('exit',()=>finish(Error('Sandbox недоступен. Запуск без изоляции запрещён.')));
        child.stdout.on('data',chunk=>{pending+=chunk;if(pending.length>4096)return finish(Error('Некорректный ответ sandbox.'));if(pending.includes('\n')){try{const result=JSON.parse(pending.trim());if(result.ready===true){if(windows){if(!Number.isInteger(result.port)||result.port<1||result.port>65535)throw Error('port');this.windowsPort=result.port;}finish();}else finish(Error('Sandbox недоступен.'+(windows&&/^[A-Za-z]{1,40}Error$/.test(result.error||'')?' '+result.error+(Number.isInteger(result.code)?' '+result.code:''):'')));}catch{finish(Error('Sandbox недоступен.'));}}});
        child.stdin.write(JSON.stringify({mode:this.config.mode,password:this.password,bridge:sandbox?'/run/igorek':this.bridge,projects:this.config.projects,vm:this.config.vm,bundle:this.bundle,profiles:sandbox?this.profilesGrant:null,root:this.root,binary_root:this.binaryRoot,owned_workspace:!!this.ownedWorkspace,assignment_direct:!!this.assignedProject,diagnostics:this.diagnostics})+'\n');
      });
      let ready=false;
      for(let attempt=0;attempt<120&&this.child===child;attempt++){
        try{const value=await this.info();if(value.app?.version?.startsWith('2.')||value.version?.startsWith('2.')){ready=true;break;}}catch{}
        await new Promise(resolve=>setTimeout(resolve,500));
      }
      if(!ready||generation!==this.generation||!this.config.enabled)throw Error('OpenCode 2.0 не ответил. Проверь sandbox или KVM.');
      await this.gateway();if(generation!==this.generation||!this.config.enabled)throw Error('Подключение OpenCode отменено.');this.state='ready';this.publish();return this.origin;
    }catch(error){await this.stop();this.state='error';this.error=error.message;this.publish();throw error;}
  }
  info(){return new Promise((resolve,reject)=>{
    const request=http.get({...this.transportOptions(),path:'/api/info',headers:{authorization:'Basic '+Buffer.from('opencode:'+this.password).toString('base64')}},res=>{
      let raw='';res.on('data',chunk=>{raw+=chunk;if(raw.length>65536)res.destroy();});res.on('end',()=>{try{if(res.statusCode!==200)throw Error('status');resolve(JSON.parse(raw));}catch{reject(Error('opencode_unavailable'));}});res.on('error',reject);
    });request.setTimeout(2000,()=>request.destroy(Error('timeout')));request.on('error',reject);
  });}
  authorized(req){const incoming=Buffer.from(req.headers.authorization||''),expected=Buffer.from('Bearer '+this.token);return incoming.length===expected.length&&timingSafeEqual(incoming,expected)&&req.headers.host===new URL(this.origin).host&&(!req.headers.origin||req.headers.origin===this.origin);}
  upstreamHeaders(req){const headers={...req.headers,host:'127.0.0.1:4096',authorization:'Basic '+Buffer.from('opencode:'+this.password).toString('base64')};if(headers.origin)headers.origin='http://127.0.0.1:4096';delete headers['proxy-authorization'];return headers;}
  async gateway(){
    this.connections=new Set();
    const server=http.createServer((req,res)=>{
      if(!this.authorized(req)||!req.url.startsWith('/')||req.url.startsWith('//')){res.writeHead(403);res.end();return;}
      const upstream=http.request({...this.transportOptions(),path:req.url,method:req.method,headers:this.upstreamHeaders(req)},reply=>{
        const headers={...reply.headers};delete headers['access-control-allow-origin'];
        if(headers.location?.startsWith('http://127.0.0.1:4096/'))headers.location=this.origin+headers.location.slice('http://127.0.0.1:4096'.length);
        res.writeHead(reply.statusCode,headers);reply.pipe(res);
      });upstream.on('error',()=>{if(!res.headersSent)res.writeHead(503);res.end('OpenCode unavailable');});req.on('aborted',()=>upstream.destroy());res.on('close',()=>upstream.destroy());req.pipe(upstream);
    });
    server.on('upgrade',(req,socket,head)=>{
      if(!this.authorized(req)){socket.destroy();return;}
      const upstream=require('node:net').connect(this.windowsPort?{host:'127.0.0.1',port:this.windowsPort}:path.join(this.bridge,'api.sock'));
      this.connections.add(upstream);upstream.on('close',()=>this.connections.delete(upstream));
      upstream.once('connect',()=>{upstream.write(req.method+' '+req.url+' HTTP/1.1\r\n'+Object.entries(this.upstreamHeaders(req)).map(([key,value])=>key+': '+value).join('\r\n')+'\r\n\r\n');if(head.length)upstream.write(head);socket.pipe(upstream);upstream.pipe(socket);});
      upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());socket.on('close',()=>upstream.destroy());
    });
    server.on('connection',socket=>{this.connections.add(socket);socket.on('close',()=>this.connections.delete(socket));});
    this.server=server;
    await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(this.config.port,'127.0.0.1',resolve);});
    this.config.port=server.address().port;this.origin='http://127.0.0.1:'+this.config.port;this.save();
  }
  closeNetwork(){for(const connection of this.connections||[])connection.destroy();this.connections?.clear();this.server?.close();this.server=null;this.egress?.shutdown();this.egress=null;}
  async stop(){
    this.generation++;const child=this.child,bridge=this.bridge,grant=this.hostServicesGrant,profiles=this.profilesGrant;this.child=null;this.bridge=null;this.hostServicesGrant=null;this.profilesGrant=null;this.closeNetwork();
    if(profiles&&this.profilesControl)await this.profilesControl('revoke',{token:profiles.token}).catch(()=>{});
    if(grant&&this.hostServicesControl)await this.hostServicesControl('revoke',{token:grant.token}).catch(()=>{});
    if(child){await new Promise(resolve=>{let timer;const done=()=>{clearTimeout(timer);resolve();};child.once('exit',done);child.stdin.end();timer=setTimeout(()=>{child.kill('SIGKILL');done();},12000);if(process.platform!=='win32')child.kill('SIGTERM');});}
    this.windowsPort=null;
    if(bridge)fs.rmSync(bridge,{recursive:true,force:true});
    this.state='stopped';this.publish();
  }
  async disable(){this.config.enabled=false;this.save();await this.stop();return this.status();}
}
module.exports={OpenCodeService,sandboxArgv,projectPath,VERSION,INTEGRITY,WINDOWS_ARCHIVE,WINDOWS_INTEGRITY};
