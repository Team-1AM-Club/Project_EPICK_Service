'use client';
import {useState, useEffect} from 'react';
import {flushSync} from 'react-dom';
import {Toaster} from '@/components/ui/sonner';
import {Experiences, Projects, Settings} from './features';
import {CompanyExplorer} from './company-explorer';
import {LoginGate} from './login-gate';
import {useAuth} from './auth-provider';
import {useWorkspaceBootstrap} from '@/lib/queries/workspace';
import type {ResumeItem} from '@/lib/queries/workspace';

const navigation = [
  {id:'companies', label:'기업 분석', asset:'nav-company'},
  {id:'projects', label:'지원 작업', asset:'nav-project'},
  {id:'experiences', label:'내 경험', asset:'nav-experience'},
];
export type ProjectPrefill = {company:string; role:string};
export default function Home() {
  return <LoginGate><WorkspaceBootstrap/></LoginGate>;
}
function WorkspaceBootstrap() {
  const bootstrap=useWorkspaceBootstrap();
  if(bootstrap.isLoading)return <main role="status" className="standard-page">개인 작업공간을 불러오고 있습니다.</main>;
  if(bootstrap.error||!bootstrap.currentUser.data)return <main role="alert" className="standard-page">개인 작업공간을 불러오지 못했습니다.</main>;
  const resume=bootstrap.resumeItems.data?.items[0]??bootstrap.home.data?.resume_items[0];
  return <><Workspace userName={bootstrap.currentUser.data.display_name} resume={resume}/><Toaster theme="dark" position="bottom-right" richColors/></>;
}
function Workspace({userName,resume}:{userName:string;resume?:ResumeItem}) {
  const initialView=resume?.resource_type==='ACTIVITY_DRAFT'?'experiences':resume?.resource_type==='PROJECT'||resume?.resource_type==='WAITING_USER_JOB'?'projects':'companies';
  const [view,setView]=useState(initialView);
  const [expanded,setExpanded]=useState(false);
  const [activeProject,setActiveProject]=useState<string|null>(resume?.resource_type==='PROJECT'?resume.resource_id:null);
  const [activeQuestion,setActiveQuestion]=useState<string|null>(null);
  const [activeJob,setActiveJob]=useState<string|null>(resume?.resource_type==='WAITING_USER_JOB'?resume.resource_id:null);
  const [returnToProject,setReturnToProject]=useState(false);
  const [newRequest,setNewRequest]=useState(0);
  const [prefill,setPrefill]=useState<ProjectPrefill>();
  const {logout}=useAuth();
  const go=(id:string)=>{setView(id==='home'?'companies':id);setExpanded(false);if(id!=='projects')setNewRequest(0);window.scrollTo({top:0,behavior:'instant'})};
  const prepare=(company:string,role:string)=>{setPrefill({company,role});setActiveProject(null);setNewRequest(n=>n+1);go('projects')};
  useEffect(()=>{
    const context=(document as Document & {modelContext?: {registerTool?: (tool: unknown, options: {signal: AbortSignal})=>unknown}}).modelContext;
    if(!context?.registerTool)return;
    const lifecycle=new AbortController();
    try{Promise.resolve(context.registerTool({name:'navigate_epick_workspace',title:'EPICK 화면 열기',description:'Open a workspace view without changing saved records.',inputSchema:{type:'object',properties:{view:{type:'string',enum:['home','experiences','companies','projects','settings']}},required:['view'],additionalProperties:false},annotations:{readOnlyHint:false},execute:(input:unknown)=>{const v=(input as {view?:string})?.view;if(!v||!['home','experiences','companies','projects','settings'].includes(v))throw new Error('Unknown workspace view');flushSync(()=>{setView(v==='home'?'companies':v);setExpanded(false);setNewRequest(0)});return{view:v,opened:true}}},{signal:lifecycle.signal})).catch(()=>{});}catch{}
    return()=>lifecycle.abort();
  },[]);
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape')setExpanded(false)};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close)},[]);
  return <div className="epick-app">
    <a href="#workspace-content" className="skip-link">본문으로 건너뛰기</a>
    {expanded&&<button className="rail-scrim" aria-label="메뉴 닫기" onClick={()=>setExpanded(false)}/>}
    <nav className={`icon-rail ${expanded?'expanded':''}`} aria-label="주 메뉴">
      <button className="rail-button menu-toggle" aria-label={expanded?'메뉴 닫기':'메뉴 열기'} aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}><img src="/figma/menu.svg" width="36" height="36" alt=""/><span>메뉴</span></button>
      <div className="rail-middle">{navigation.map(n=><button key={n.id} className={`rail-button ${view===n.id?'active':''}`} aria-label={n.label} title={n.label} aria-current={view===n.id?'page':undefined} onClick={()=>go(n.id)}><span className="rail-icon"><img src={`/figma/${n.asset}.svg`} width="36" height="36" alt=""/></span><span>{n.label}</span></button>)}</div>
      <button className={`rail-button ${view==='settings'?'active':''}`} aria-label="프로필 및 설정" title="프로필 및 설정" onClick={()=>go('settings')}><span className="rail-icon"><img src="/figma/nav-project.svg" width="36" height="36" alt=""/></span><span>프로필</span></button>
      <button className="rail-button" aria-label="로그아웃" title="로그아웃" onClick={()=>void logout()}><span>로그아웃</span></button>
    </nav>
    <main id="workspace-content" className="workspace-main">
      <p className="sr-only">{userName}님의 개인 작업공간</p>
      <div hidden={view!=='companies'}><CompanyExplorer onPrepare={prepare}/></div>
      {view!=='companies'&&<div className="standard-page"><button className="epick-brand" aria-label="EPICK 기업 분석으로" onClick={()=>go('companies')}><img src="/figma/epick.svg" alt="EPICK" width="104" height="27"/></button>
        {view==='experiences'?<Experiences resumeActivityId={resume?.resource_type==='ACTIVITY_DRAFT'?resume.resource_id:undefined} onReturn={returnToProject?()=>{setReturnToProject(false);go('projects')}:undefined}/>:view==='projects'?<Projects activeId={activeProject} onActive={setActiveProject} activeQuestionId={activeQuestion} onActiveQuestion={setActiveQuestion} activeJobId={activeJob} onActiveJob={setActiveJob} newRequest={newRequest} prefill={prefill} onAddExperience={()=>{setReturnToProject(true);go('experiences')}}/>:<Settings/>}

      </div>}
    </main>
  </div>;
}
