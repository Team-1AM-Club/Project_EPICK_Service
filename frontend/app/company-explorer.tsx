'use client';
import {useState} from 'react';
import {ArrowUpRight, Plus, Check, Loader2, FileText} from 'lucide-react';
import {Dialog,DialogContent,DialogHeader,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {Tabs,TabsList,TabsTrigger,TabsContent} from '@/components/ui/tabs';
import {toast} from 'sonner';

type Company={id:string;name:string;logo?:string;category:string};
const initialCompanies:Company[]=[
  {id:'samsung-bio',name:'삼성바이오로직스',logo:'samsung',category:'디지털 제품 · 플랫폼'},
  {id:'samsung-sdi',name:'삼성SDI',logo:'samsung',category:'디지털 제품 · 플랫폼'},
  {id:'naver',name:'네이버 주식회사',logo:'naver',category:'디지털 제품 · 플랫폼'},
  {id:'apple',name:'Apple Seoul',logo:'apple',category:'디지털 제품 · 플랫폼'},
  {id:'samsung',name:'삼성전자',logo:'samsung',category:'디지털 제품 · 플랫폼'},
];
const stack=[{name:'Java',asset:'java'},{name:'Kotlin',asset:'kotlin'},{name:'MySQL',asset:'mysql'},{name:'Redis',asset:'redis'},{name:'Adobe Illustrator',asset:'illustrator'},{name:'Figma',asset:'figma'}];
function CompanyLogo({company}:{company:Company}){return <span className={`figma-company-logo ${company.logo||'custom'}`}>{company.logo?<img src={`/figma/${company.logo}.svg`} alt="" width={company.logo==='samsung'?40:company.logo==='apple'?25:23} height={company.logo==='samsung'?11:26}/>:company.name.slice(0,1)}</span>}
function Badge({children,ai=false}:{children:React.ReactNode;ai?:boolean}){return <span className={`source-badge ${ai?'ai':''}`}>{children}</span>}
function TechStack({items=stack}:{items?:typeof stack}){return <div className="tech-list">{items.map(t=><div className="tech-item" key={t.name}><span className={`tech-logo tech-${t.asset}`}><img src={`/figma/${t.asset}.png`} alt="" width="36" height="36"/></span><span>{t.name}</span></div>)}</div>}
function Quotation({children}:{children:React.ReactNode}){return <div className="figma-quotation"><img src="/figma/quote-open.svg" width="16" height="12" alt=""/><p>{children}</p><img src="/figma/quote-close.svg" width="16" height="12" alt=""/></div>}
export function CompanyExplorer({onPrepare}:{onPrepare:(company:string,role:string)=>void}){
  const [companies,setCompanies]=useState(initialCompanies);
  const [selected,setSelected]=useState('naver');
  const [tab,setTab]=useState('summary');
  const [adding,setAdding]=useState(false);
  const [name,setName]=useState('');
  const [source,setSource]=useState<{title:string;text:string}|null>(null);
  const [choices,setChoices]=useState<Record<string,string>>({});
  const [refreshing,setRefreshing]=useState(false);
  const [updated,setUpdated]=useState(false);
  const company=companies.find(c=>c.id===selected)!;
  const isSample=selected==='naver';
  const choice=choices[selected]||'pending';
  const showSource=(title:string,text:string)=>setSource({title,text});
  const reanalyze=()=>{setRefreshing(true);window.setTimeout(()=>{setRefreshing(false);setUpdated(true)},1100)};
  const selectCompany=(id:string)=>{setSelected(id);setTab('summary');setUpdated(false)};
  const add=(e:React.FormEvent)=>{e.preventDefault();const trimmed=name.trim();if(!trimmed)return;const existing=companies.find(c=>c.name===trimmed);if(existing){selectCompany(existing.id)}else{const id=crypto.randomUUID();setCompanies(cs=>[...cs,{id,name:trimmed,category:'직접 추가한 기업'}]);selectCompany(id)}setAdding(false);setName('');toast.success('기업을 열었어요.')};
  return <div className="company-explorer">
    <aside className="company-sidebar" aria-label="기업 목록"><div className="epick-brand"><img src="/figma/epick.svg" alt="EPICK" width="104" height="27"/></div>
      <button className="add-company" onClick={()=>setAdding(true)}><Plus size={14}/> 기업 추가</button>
      <div className="company-picker">{companies.map(c=><button key={c.id} className={`company-option ${c.id===selected?'selected':''}`} aria-pressed={c.id===selected} onClick={()=>selectCompany(c.id)}><CompanyLogo company={c}/><span><strong>{c.name}</strong><small>{c.category}</small></span></button>)}</div>
    </aside>
    <div className="company-main">
      <div className={`company-banner ${!isSample?'empty-banner':''}`}>{isSample&&<img src="/figma/banner.png" alt="" width="1529" height="259"/>}</div>
      <div className="company-content">
        <section className="company-profile glass-panel" aria-label="기업 소개"><div className="company-profile-copy"><CompanyLogo company={company}/><p>{isSample?'테크놀로지, 인포메이션, 인터넷 · 경기도 성남시':'기업 정보 · 수집 전'}</p><h1>{company.name}{isSample&&<img src="/figma/verified.svg" width="13" height="10" alt=""/>}</h1></div>{isSample&&<div className="company-photos"><img src="/figma/conference.png" alt="네이버 행사" width="175" height="130"/><img src="/figma/building.png" alt="네이버 사옥" width="175" height="130"/></div>}</section>
        <div className="update-row"><span>{updated?'방금 확인':isSample?'마지막 업데이트 2026.8.9':'아직 수집된 자료가 없어요'}</span><button disabled={refreshing} onClick={reanalyze}>{refreshing?<><Loader2 size={12} className="animate-spin"/> 확인 중</>:'재분석 요청'}</button></div>
        <Tabs value={tab} onValueChange={setTab} className="company-tabs"><TabsList aria-label="기업 정보 종류"><TabsTrigger value="summary">요약</TabsTrigger><TabsTrigger value="evidence">근거</TabsTrigger><TabsTrigger value="jobs">채용 공고</TabsTrigger><TabsTrigger value="conflicts">상충 자료</TabsTrigger></TabsList>
          <TabsContent value="summary">{isSample?<>
            <section className="analysis-section"><h2>인재상 <Badge>원문 근거</Badge></h2><button className="glass-panel quote-button" onClick={()=>showSource('인재상 원문 근거','함께 문제를 해결하는 사람, 빠르게 학습하는 사람')}><Quotation>함께 문제를 해결하는 사람, 빠르게 학습하는 사람</Quotation></button></section>
            <section className="analysis-section"><h2>주요 기술 스택 <Badge>원문 근거</Badge></h2><div className="glass-panel tech-panel"><TechStack/></div></section>
            <section className="analysis-section"><h2>채용 우선순위 <Badge ai>AI 해석</Badge><span className="pending-badge">{choice==='use'?'활용 선택됨':choice==='skip'?'활용 제외됨':'승인 대기'}</span></h2><button className="glass-panel quote-button" onClick={()=>showSource('채용 우선순위 · AI 해석','클라우드/분산 시스템 경험을 일관되게 강조하는 패턴')}><Quotation>클라우드/분산 시스템 경험을 일관되게 강조하는 패턴</Quotation></button><div className="interpretation-actions">{choice==='pending'?<><button onClick={()=>setChoices({...choices,[selected]:'use'})}><Check size={13}/> 이 해석 활용하기</button><button onClick={()=>setChoices({...choices,[selected]:'skip'})}>이번에는 제외</button></>:<button onClick={()=>setChoices({...choices,[selected]:'pending'})}>선택 변경</button>}<small>활용 여부만 선택하며, 사실 확인을 뜻하지 않아요.</small></div></section>
          </>:<div className="glass-panel company-empty"><FileText size={26}/><h2>기업 자료를 기다리고 있어요</h2><p>{company.name}의 등록된 자료가 없어요.<br/>지원할 직무와 문항은 먼저 정리할 수 있어요.</p><button className="primary" onClick={()=>onPrepare(company.name,'')}>이 기업으로 지원 준비하기 <ArrowUpRight size={16}/></button></div>}</TabsContent>
          <TabsContent value="evidence">{isSample?<>{[{title:'인재상',text:'함께 문제를 해결하는 사람, 빠르게 학습하는 사람',source:'네이버 공식 채용 홈페이지'},{title:'기술 방향',text:'클라우드 네이티브 전환을 가속화하며, 내부 시스템을 컨테이너 기반으로 전환할 예정입니다.',source:'네이버 IR 보도자료'},{title:'채용 우선순위 해석',text:'공고 패턴과 기술 스택 언급 빈도를 종합하면, 클라우드/분산 시스템 경험을 일관되게 강조하는 경향이 확인됩니다.',source:'여러 자료를 종합한 해석'}].map((s,i)=><section className="analysis-section" key={s.title}><h2>{s.title} {i===2&&<Badge ai>AI 해석</Badge>}<Badge>원문 근거</Badge></h2><button className="glass-panel evidence-panel quote-button" onClick={()=>showSource(s.title,s.text)}><Quotation>{s.text}</Quotation><span className="evidence-meta">{s.source} <ArrowUpRight size={13}/> <span>2026.09.06 수집</span></span></button></section>)}</>:<div className="company-empty glass-panel"><FileText/><h2>확인할 근거가 없어요</h2></div>}</TabsContent>
          <TabsContent value="jobs">{isSample?<div className="job-examples">{[{role:'백엔드 엔지니어',items:stack.slice(0,4)},{role:'그래픽 디자이너',items:stack.slice(4)}].map((job,i)=><article className={`glass-panel hiring-card hiring-${i}`} key={job.role}><h2>{job.role} · 채용 공고 <Badge>원문 근거</Badge></h2><p>2026.09.01 게시 · 2026.09.06 수집</p><TechStack items={job.items}/><button className="job-prepare" onClick={()=>onPrepare(company.name,job.role)}>이 공고로 지원 준비하기 <ArrowUpRight size={16}/></button></article>)}</div>:<div className="company-empty glass-panel"><FileText/><h2>연결된 채용 공고가 없어요</h2><p>직무와 문항을 직접 입력해 지원을 준비할 수 있어요.</p><button className="primary" onClick={()=>onPrepare(company.name,'')}>지원 준비하기 <ArrowUpRight size={16}/></button></div>}</TabsContent>
          <TabsContent value="conflicts"><div className="company-empty glass-panel"><Check size={26}/><h2>표시할 상충 자료가 없어요</h2><p>자료마다 내용이 다를 때 이곳에서 비교할 수 있어요.</p></div></TabsContent>
        </Tabs>

      </div>
      {isSample&&<div className="social-links" aria-label="기업 소셜 링크">{['linkedin','x','instagram'].map(s=><button key={s} aria-label={s}><img src={`/figma/${s}.svg`} width="20" height="20" alt=""/></button>)}</div>}
    </div>
    <Dialog open={adding} onOpenChange={setAdding}><DialogContent><DialogHeader><DialogTitle>기업 추가</DialogTitle><DialogDescription>관심 있는 기업을 추가해 지원을 준비하세요.</DialogDescription></DialogHeader><form className="experience-form" onSubmit={add}><label>기업명<input autoFocus required maxLength={80} value={name} onChange={e=>setName(e.target.value)} placeholder="기업 이름을 입력해 주세요"/></label><button className="primary" type="submit">기업 추가 <Plus size={16}/></button></form></DialogContent></Dialog>
    <Dialog open={!!source} onOpenChange={o=>!o&&setSource(null)}><DialogContent aria-describedby={undefined}><DialogHeader><DialogTitle>{source?.title}</DialogTitle></DialogHeader><blockquote className="evidence-quote">{source?.text}</blockquote></DialogContent></Dialog>
  </div>;
}
