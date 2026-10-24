// Presentation only: all market and investment decisions come from the producer.
const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const number = value => typeof value !== "boolean" && value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
const labels = {delayed:"관측 지연",current:"현재 근거",latest_available:"최근 발표값",complete:"자료 확인 완료",partial:"일부 자료 확인 필요",success:"동기화 완료",passed:"검토 완료",fresh:"최신 관측",market_closed:"최근 종가",stale:"직전값",expired:"만료",unavailable:"확인 불가",unknown:"확인 불가",pending:"검토 대기",insufficient:"근거 부족"};
export function normalizeHistory(history) {
  const points = new Map();
  for (const point of Array.isArray(history) ? history : []) {
    if (!point || typeof point !== "object") continue;
    const date = String(point.date || point.at || point.observed_at || "");
    const value = number(point.value ?? point.close);
    if (date && Number.isFinite(Date.parse(date)) && value !== null) points.set(date, {date, value});
  }
  return [...points.values()].sort((a,b) => Date.parse(a.date)-Date.parse(b.date)).slice(-60);
}
export function metricState(metric) {
  if (!metric || number(metric.current ?? metric.price ?? metric.value) === null) return "unavailable";
  if (["expired", "delayed"].includes(metric.data_status || metric.freshness_status)) return metric.data_status || metric.freshness_status;
  if (metric.is_stale && metric.data_status !== "latest_available") return "stale";
  const state = metric.data_status || metric.freshness_status;
  if (state) return Object.hasOwn(labels,state) ? state : "unknown";
  return "unknown";
}
export function chartModel(history, width=560, height=220) {
  const points=normalizeHistory(history);
  if(points.length<2) return null;
  const values=points.map(p=>p.value),low=Math.min(...values),high=Math.max(...values);
  const spread=high-low || Math.max(Math.abs(high)*0.01,1);
  const minimum=low-spread*0.1, maximum=high+spread*0.1;
  const start=Date.parse(points[0].date),duration=Date.parse(points.at(-1).date)-start || 1;
  return {points,minimum,maximum,path:points.map((p,i)=>`${i?"L":"M"}${(42+(Date.parse(p.date)-start)/duration*(width-58)).toFixed(2)},${(16+(maximum-p.value)/(maximum-minimum)*(height-48)).toFixed(2)}`).join(" ")};
}
function dateText(value) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return value + " 기준";
  if (!value || !Number.isFinite(Date.parse(value))) return "관측 시각 확인 불가";
  return new Intl.DateTimeFormat("ko-KR",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Seoul"}).format(new Date(value))+" KST";
}
export function observationText(metric) {
  if (metric?.observation_precision === "month" && /^\d{4}-\d{2}$/.test(metric.reference_period || "")) return metric.reference_period + " 기준월";
  return dateText(metric?.observed_at || metric?.data_as_of);
}
function unitText(unit) { return ({people:"건",index:"지수","%":"%"})[unit] || unit || ""; }
function valueText(metric) {
  const value=number(metric?.current ?? metric?.price ?? metric?.value);
  return value===null ? "—" : value.toLocaleString("ko-KR",{maximumFractionDigits:2})+(metric.unit==="%"?"%":metric.unit==="people"?"건":metric.unit==="index"?" 지수":"");
}
// Reading aids describe observed data; they never produce trading signals.
const metricGuides = {
  us_2y: {definition:"미국 2년 만기 국채의 시장금리입니다.",meaning:"오르면 시장이 예상하는 단기 금리 부담이 커지는 쪽입니다.",watch:"10년 실질금리와 함께 보세요. 금리 하락이 물가 안정 때문인지 경기 둔화 때문인지 구분합니다.",cadence:"영업일 관측 · 하루 단위 자료"},
  us_real_10y: {definition:"물가에 연동되는 미국 10년 국채의 시장금리입니다.",meaning:"오르면 미래 이익에 부여하는 현재 가치에 부담이 될 수 있습니다.",watch:"성장주의 이익 전망도 함께 확인하세요. 금리 상승만으로 기업의 장기 성장 훼손을 단정하지 않습니다.",cadence:"영업일 관측 · 하루 단위 자료"},
  usd_broad: {definition:"여러 교역 상대국 통화에 대한 달러의 전반적인 강도를 나타냅니다.",meaning:"오르면 여러 통화에 비해 달러가 강해지는 방향입니다.",watch:"원·달러 환율도 함께 오르는지 보세요. 미국 주식의 달러 수익과 원화 환산 수익은 다를 수 있습니다.",cadence:"일별 관측 · 주간 발표"},
  nfci: {definition:"미국 금융시장에서 돈을 구하기 쉬운지 보여주는 종합지표입니다.",meaning:"0보다 높으면 장기 평균보다 긴축적이고, 낮으면 완화적입니다.",watch:"수준과 방향을 구분하세요. 0 아래라도 빠르게 오르면 자금 조달 여건의 변화를 확인합니다.",cadence:"주간 발표"},
  initial_claims: {definition:"미국에서 처음 실업수당을 신청한 청구 건수입니다.",meaning:"4주 평균이 계속 오르면 고용이 약해지는지 살펴볼 필요가 있습니다.",watch:"월간 실업률도 함께 보세요. 한 주의 급증은 휴일·재해 등의 영향인지 확인합니다.",cadence:"주간 발표 · 계절조정"},
  core_cpi: {definition:"식품·에너지를 제외한 소비자 물가입니다. 지수 수준과 물가상승률은 다릅니다.",meaning:"3개월 연율은 최근 3개월의 물가 상승 속도가 1년간 이어질 때의 비율입니다.",watch:"근원 PCE와 상승 속도를 함께 보세요. 물가 둔화와 기업의 매출·이익 둔화를 구분합니다.",cadence:"월간 발표 · 계절조정"},
  core_pce: {definition:"식품·에너지를 제외한 소비지출 물가입니다. 지수 수준 자체는 상승률이 아닙니다.",meaning:"3개월 연율이 낮아지면 최근 물가 상승 속도가 느려진 것입니다.",watch:"연준의 2% 목표는 전체 PCE의 연간 상승률 기준입니다. 근원 PCE의 3개월 연율과 직접 같게 보지 마세요.",cadence:"월간 발표 · 계절조정"},
  unemployment: {definition:"일할 의사가 있는 경제활동인구 중 일자리를 찾는 실업자의 비율입니다.",meaning:"상승이 이어지면 고용과 소비가 약해지는지 확인합니다.",watch:"신규 실업수당의 4주 평균과 함께 보세요. 실업률 상승만으로 침체를 확정하지 않습니다.",cadence:"월간 발표 · 계절조정"},
  vix: {definition:"S&P 500 옵션에 반영된 향후 30일의 예상 변동성입니다.",meaning:"오르면 시장이 더 큰 가격 변동을 예상한다는 뜻입니다. 주가 방향을 예측하는 값은 아닙니다.",watch:"QQQ·KOSPI의 가격 추세와 함께 보세요. 공포가 크다는 이유만으로 저평가를 단정하지 않습니다.",cadence:"시장 관측 · 장중 수집"},
  qqq: {definition:"나스닥 100 지수를 추종하는 ETF의 가격입니다.",meaning:"20일 평균은 최근 약 한 달의 가격 흐름을 비교하는 기준입니다.",watch:"이익 전망과 밸류에이션도 확인하세요. 평균 아래라는 이유만으로 싸다고 판단하지 않습니다.",cadence:"미국장 관측 · 장중 수집"},
  kospi: {definition:"한국 유가증권시장의 전반적인 가격 흐름을 나타냅니다.",meaning:"20일 평균은 최근 약 한 달의 가격 흐름을 비교하는 기준입니다.",watch:"환율과 기업 이익 전망을 함께 보세요. 지수 하락과 보유 기업의 경쟁력 약화를 구분합니다.",cadence:"한국장 관측 · 장중 수집"},
  usdkrw: {definition:"1달러를 사는 데 필요한 원화 금액입니다.",meaning:"오르면 원화 약세입니다. 같은 달러 자산을 살 때 더 많은 원화가 필요합니다.",watch:"광의 달러지수와 함께 보세요. 달러 전반의 강세인지 원화 고유의 약세인지 구분합니다.",cadence:"외환시장 관측 · 장중 수집"},
};
const signed = (value,unit="") => `${value>0?"+":""}${value.toLocaleString("ko-KR",{maximumFractionDigits:2})}${unit}`;
const monthIndex = value => {const match=String(value||"").match(/^(\d{4})-(\d{2})/);return match?Number(match[1])*12+Number(match[2])-1:null;};
export function metricReading(id,metric={}) {
  const guide=metricGuides[id] || {definition:"원천 관측값입니다.",meaning:"비교 기준을 확인한 뒤 해석하세요.",watch:"출처와 관측 시각을 먼저 확인하세요.",cadence:"발표 주기 확인 필요"};
  const current=number(metric.current ?? metric.price ?? metric.value),state=metricState(metric);
  const asOf=metric.observed_at || metric.data_as_of || metric.reference_period;
  const stamp=Date.parse(asOf),history=normalizeHistory(metric.history).filter(p=>Number.isFinite(stamp)&&Date.parse(p.date)<=stamp);
  let comparison="비교 자료 부족 · 이전 관측값 확인 필요",meaning=guide.meaning;
  const comparisons=[];
  const anchorMonth=monthIndex(asOf);
  const monthly=id==="unemployment"||id==="core_cpi"||id==="core_pce";
  const prior=monthly ? history.find(p=>monthIndex(p.date)===anchorMonth-1) : history.filter(p=>Date.parse(p.date)<=stamp-30*86400000&&Date.parse(p.date)>=stamp-38*86400000).at(-1);
  if(current!==null) {
    if(id==="core_cpi"||id==="core_pce") {
      const trend=number(metric.trend_3m_annualized_pct);
      if(trend!==null) comparisons.push(`최근 3개월 연율 ${trend.toFixed(2)}%`);
      const months=history.slice(-7);
      if(trend!==null&&months.length===7&&monthIndex(months.at(-1).date)===anchorMonth&&months.every((p,i)=>i===0||monthIndex(p.date)-monthIndex(months[i-1].date)===1)&&months[0].value>0&&months[3].value>0) {
        const previousTrend=((months[3].value/months[0].value)**4-1)*100;
        comparisons.push(`직전 3개월 연율 ${previousTrend.toFixed(2)}%`);
      }
      if(prior && prior.value>0)comparisons.push(`전월 대비 ${signed((current/prior.value-1)*100,"%")}`);
      const yearAgo=history.find(p=>monthIndex(p.date)===anchorMonth-12);
      if(yearAgo?.value>0)comparisons.push(`전년 같은 달 대비 ${signed((current/yearAgo.value-1)*100,"%")}`);
      if(!comparisons.length)comparisons.push("물가지수 수준 · 상승률 비교 자료 부족");
    } else if(id==="initial_claims") {
      const avg=number(metric.four_week_average);
      if(avg!==null) comparisons.push(`최근 4주 평균 ${avg.toLocaleString("ko-KR")}건`);
      const weeks=history.slice(-8);
      if(avg!==null&&weeks.length===8&&weeks.at(-1).date.slice(0,10)===String(asOf).slice(0,10)&&weeks.every((p,i)=>i===0||Date.parse(p.date)-Date.parse(weeks[i-1].date)===7*86400000)) {
        const before=weeks.slice(0,4).reduce((sum,p)=>sum+p.value,0)/4;
        comparisons.push(`이전 4주 평균 대비 ${signed(avg-before,"건")}`);
      }
      if(!comparisons.length)comparisons.push("4주 평균 비교 자료 부족");
    } else if(["qqq","kospi"].includes(id) && number(metric.ma20)!==null && number(metric.ma20)>0) {
      comparisons.push(`20일 평균 ${Number(metric.ma20).toLocaleString("ko-KR",{maximumFractionDigits:2})} 대비 ${signed((current/Number(metric.ma20)-1)*100,"%")}`);
    } else if(id==="vix" && number(metric.ma5)!==null) {
      comparisons.push(`5일 평균 ${Number(metric.ma5).toFixed(2)} 대비 ${signed(current-Number(metric.ma5),"p")}`);
    } else if(id==="usdkrw" && number(metric.change_pct)!==null) {
      comparisons.push(`전일 대비 ${signed(Number(metric.change_pct),"%")}`);
    }
    if(prior&&!['core_cpi','core_pce','initial_claims','qqq','kospi','vix','usdkrw'].includes(id)) {
      const delta=current-prior.value;
      const unit=metric.unit==="%"?"%p":"p";
      const change=id==="usd_broad"&&prior.value>0?signed((current/prior.value-1)*100,"%"):signed(delta,unit);
      comparisons.push(`${monthly?"전월":"약 1개월 전"} ${prior.value.toLocaleString("ko-KR",{maximumFractionDigits:2})}${metric.unit==="%"?"%":""} 대비 ${change} (${prior.date.slice(0,10)} 기준)`);
    }
    if(prior&&["us_2y","us_real_10y","usd_broad","unemployment"].includes(id)&&current!==prior.value) {
      const rising=current>prior.value;
      const descriptions={
        us_2y:rising?"약 한 달 전보다 2년 금리가 올랐습니다. 시장의 단기 금리 부담이 커지는지 확인하세요.":"약 한 달 전보다 2년 금리가 내렸습니다. 물가 안정 기대와 경기 둔화 우려를 함께 확인하세요.",
        us_real_10y:rising?"약 한 달 전보다 실질금리가 올랐습니다. 미래 이익의 현재 가치에 부담이 될 수 있습니다.":"약 한 달 전보다 실질금리가 내렸습니다. 보유 기업의 이익 전망도 함께 확인하세요.",
        usd_broad:rising?"약 한 달 전보다 달러가 전반적으로 강해졌습니다. 원·달러 환율과 같은 방향인지 확인하세요.":"약 한 달 전보다 달러가 전반적으로 약해졌습니다. 원·달러 환율과 같은 방향인지 확인하세요.",
        unemployment:rising?"전월보다 실업률이 올랐습니다. 실업수당 청구도 늘어나는지 확인하세요.":"전월보다 실업률이 내렸습니다. 실업수당 청구와 함께 고용 흐름을 확인하세요.",
      };
      meaning=descriptions[id];
    }
    if(id==="nfci") {
      comparisons.unshift("기준 0 · 장기 평균 금융여건");
      meaning=current>0?"장기 평균보다 돈을 구하기 어려운 여건입니다.":current<0?"장기 평균보다 돈을 구하기 쉬운 여건입니다.":"장기 평균 수준의 금융여건입니다.";
    }
    if(comparisons.length)comparison=comparisons.join(" · ");
  } else {
    comparison="현재 값 확인 불가 · 비교와 해석 보류";
    meaning="유효한 관측값이 없어 현재 상황을 해석할 수 없습니다.";
  }
  const qualified=["stale","delayed","expired"].includes(state);
  if(current!==null&&(!Number.isFinite(stamp)||["unknown","unavailable"].includes(state))) {
    comparison=`검증 전 자료 · ${comparison}`;
    meaning="자료의 유효성 또는 관측 기준을 확인할 수 없어 현재 해석을 보류합니다.";
  }
  return {...guide,comparison,meaning:qualified&&current!==null?`직전 자료 기준 · ${meaning}`:meaning,state};
}
function readingHtml(id,metric) {
  const reading=metricReading(id,metric);
  return `<dl class="metric-reading"><div><dt>무엇을 보나요</dt><dd>${escape(reading.definition)}</dd></div><div><dt>비교 기준</dt><dd>${escape(reading.comparison)}</dd></div><div><dt>어떻게 읽나요</dt><dd>${escape(reading.meaning)}</dd></div><div><dt>함께 확인할 점</dt><dd>${escape(reading.watch)}</dd></div></dl>${metric.unit==="%"?'<p class="metric-meta">%p는 비율의 차이입니다. 예: 4.0% → 4.2%는 +0.2%p.</p>':""}<p class="metric-meta">${escape(reading.cadence)} · 관측값 비교이며 매매 신호가 아닙니다.</p>`;
}

function safeLink(url) { try { return new URL(url).protocol==="https:" ? url : null; } catch { return null; } }
function sourceHtml(metric) { const url=safeLink(metric?.source_url);return url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(metric.source || "자료 출처")} ↗</a>` : ""; }
function openDialog(dialog, opener) {
  dialog._opener=opener || document.activeElement;
  if (!dialog.open) dialog.showModal();
}
function makeDialog(id,title,body) {
  const dialog=document.createElement("dialog");dialog.id=id;dialog.className="sayo-dialog";dialog.setAttribute("aria-labelledby",`${id}-title`);
  dialog.innerHTML=`<div class="dialog-head"><h2 id="${id}-title">${title}</h2><button type="button" class="dialog-close" aria-label="닫기">✕</button></div>${body}`;
  document.body.append(dialog);
  dialog.querySelector(".dialog-close").addEventListener("click",()=>dialog.close());
  dialog.addEventListener("close",()=>{
    const key=dialog._opener?.dataset?.series;
    const opener=dialog._opener?.isConnected?dialog._opener:key?[...document.querySelectorAll("[data-series]")].find(button=>button.dataset.series===key):null;
    opener?.focus();
  });
  dialog.addEventListener("click",event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
  return dialog;
}
export function preferenceStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}
export function studyReportMeta(data) {
  const report=data?.cycle_report || {};
  const quality=report.quality_status;
  const status=report.data_status || data?.cycle_sync_status || data?.cycle_quality_status || (["unavailable","expired","stale"].includes(quality)?quality:null);
  return {status,quality,summary:status?`${labels[status] || "확인 필요"}${quality?` · ${labels[quality] || "검토 상태 확인"}`:""}`:"종목별 사이클과 근거를 펼쳐 확인하세요.",generated_at:report.generated_at || data?.cycle_generated_at,expires_at:report.expires_at};
}
export function cycleLabel(stock) {
  const base={favorable:"사이클 우호",neutral:"사이클 중립",caution:"사이클 주의",pending:"사이클 갱신 대기"};
  const status=stock?.cycle_status;
  if(!base[status]) return null;
  if(stock.cycle_data_status && !["fresh","market_closed","current","latest_available"].includes(stock.cycle_data_status)) return "사이클 갱신 대기";
  const reasons={insufficient:"근거 부족",conflicting:"근거 상충",awaiting_independent_review:"독립 검토 대기"};
  return base[status]+(status==="neutral"&&reasons[stock.cycle_assessment_status]?` · ${reasons[stock.cycle_assessment_status]}`:"");
}
export function renderCycleContext(stock) {
  const hypotheses=Array.isArray(stock.cycle_hypotheses)?stock.cycle_hypotheses:[];
  const changes=Array.isArray(stock.cycle_changes)?stock.cycle_changes:[];
  const evidence=stock.cycle_evidence || [];
  const refs=Array.isArray(evidence)?evidence:[...(Array.isArray(evidence.supporting)?evidence.supporting:[]),...(Array.isArray(evidence.contrary)?evidence.contrary:[])];
  const statusNames={favorable:"우호",neutral:"중립",caution:"주의",pending:"검토 대기",sufficient:"근거 충족",conflicting:"근거 상충",insufficient:"근거 부족",awaiting_independent_review:"독립 검토 대기"};
  const text=item=>{
    if(typeof item==="string")return item;
    if(!item || typeof item!=="object")return "";
    const description=item.summary||item.fact||item.question||item.label;
    if(description)return description;
    if(item.from||item.to){const before=statusNames[item.from]||item.from||"이전 판정 없음",after=statusNames[item.to]||item.to||"판정 대기";const assessment=item.assessment_to?` · ${statusNames[item.assessment_to]||item.assessment_to}`:"";return `${before} → ${after}${assessment}`;}
    return "";
  };
  const visibleChanges=changes.map(text).filter(Boolean).slice(0,4),visibleHypotheses=hypotheses.map(text).filter(Boolean).slice(0,4);
  const safeRefs=refs.filter(item=>item&&typeof item==="object").slice(0,6).map(item=>{const url=safeLink(item.source_url||item.url),direction=item.direction==="negative"?"반대 근거":item.direction==="positive"?"지지 근거":"근거";return `<div class="research-evidence"><span class="research-evidence-label">${direction}</span><p>${escape(text(item))}</p><div class="metric-meta">${url?` <a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(item.source_name||"원문")} ↗</a>`:""}${item.published_at?` <span class="metric-meta">${escape(item.published_at)}</span>`:""}</div></div>`;}).join("");
  if(!stock.cycle_reason&&!visibleHypotheses.length&&!visibleChanges.length&&!refs.length)return "";
  return `<section class="research-context"><h3>산업·거시 점검</h3><p>${escape(cycleLabel(stock) || "사이클 확인 대기")}</p>${stock.cycle_reason?`<p>${escape(stock.cycle_reason)}</p>`:""}${visibleChanges.length?`<h4>이번 점검의 변화</h4>${visibleChanges.map(item=>`<p>${escape(item)}</p>`).join("")}`:""}${visibleHypotheses.length?`<h4>확인할 보유 근거</h4>${visibleHypotheses.map(item=>`<p>${escape(item)}</p>`).join("")}`:""}${safeRefs}${stock.cycle_last_reviewed_at?`<p>마지막 검토 ${escape(dateText(stock.cycle_last_reviewed_at))}</p>`:""}${stock.cycle_next_check_at?`<p>다음 확인 ${escape(dateText(stock.cycle_next_check_at))}</p>`:""}</section>`;
}
export function createSayoUI({getPushState,setPushEnabled,storage=preferenceStorage()}={}) {
  let latest={},activeMetric=null;
  const metricNames={vix:"VIX",qqq:"QQQ",kospi:"KOSPI",usdkrw:"USD/KRW",us_2y:"미국 2년 국채금리",us_real_10y:"미국 10년 실질금리",usd_broad:"광의 달러지수",nfci:"금융여건 NFCI",initial_claims:"신규 실업수당 청구",core_cpi:"근원 CPI 지수",core_pce:"근원 PCE 지수",unemployment:"실업률"};
  const preferenceKey="sayo:theme";
  const applyTheme=theme=>{
    document.documentElement.dataset.theme=theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content",theme==="light"?"#f7f5f1":"#221f1d");
    try{storage?.setItem(preferenceKey,theme);}catch{}
  };
  try{applyTheme(storage?.getItem(preferenceKey)==="light"?"light":"dark");}catch{applyTheme("dark");}
  const settings=makeDialog("settings-dialog","설정",`<div class="settings-row"><div><h3>어두운 화면</h3><p>끄면 밝은 화면으로 전환합니다.</p></div><input id="theme-toggle" type="checkbox" role="switch" aria-label="어두운 화면" checked></div><div class="settings-row"><div><h3>푸시 알림</h3><p id="push-setting-description">알림 설정을 확인합니다.</p></div><input id="push-toggle" type="checkbox" role="switch" aria-label="푸시 알림"></div><p id="settings-message" class="settings-message" role="status" aria-live="polite"></p>`);
  const themeToggle=settings.querySelector("#theme-toggle"),pushToggle=settings.querySelector("#push-toggle"),pushDescription=settings.querySelector("#push-setting-description"),message=settings.querySelector("#settings-message");
  themeToggle.addEventListener("change",()=>applyTheme(themeToggle.checked?"dark":"light"));
  async function refreshPush() {
    const state=await getPushState?.() || {registered:false,supported:false,enabled:false};
    pushToggle.checked=state.enabled===true;
    pushToggle.disabled=!state.registered || !state.supported;
    pushDescription.textContent=!state.supported?"이 환경은 웹 푸시를 지원하지 않습니다.":!state.registered?"알림 등록과 승인을 마치면 설정할 수 있습니다.":state.permission!=="granted"?"브라우저 알림 권한이 꺼져 있어 알림이 표시되지 않습니다. 이 스위치를 끄면 서버 수신 설정도 해제합니다.":"이 기기의 알림 수신을 설정합니다. 화면 접근 승인은 유지됩니다.";
    return state;
  }
  pushToggle.addEventListener("change",async()=>{
    const desired=pushToggle.checked;pushToggle.disabled=true;message.textContent="알림 설정 저장 중…";
    try{await setPushEnabled?.(desired);message.textContent=desired?"푸시 알림을 켰습니다.":"푸시 알림을 껐습니다. 화면은 계속 사용할 수 있습니다.";}
    catch{pushToggle.checked=!desired;message.textContent="설정을 저장하지 못했습니다. 기존 설정을 유지합니다.";}
    finally{try{await refreshPush();}catch{message.textContent="설정 상태를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.";pushToggle.disabled=true;}}
  });
  document.querySelector("#settings-open")?.addEventListener("click",async event=>{themeToggle.checked=document.documentElement.dataset.theme!=="light";message.textContent="";openDialog(settings,event.currentTarget);try{await refreshPush();}catch{pushToggle.disabled=true;pushDescription.textContent="알림 설정을 확인할 수 없습니다.";}});
  const metricDialog=makeDialog("metric-dialog","지표 상세",`<div class="view-switch" role="group" aria-label="지표 보기 방식"><button type="button" data-view="value" aria-pressed="true">값</button><button type="button" data-view="chart" aria-pressed="false">그래프</button></div><div id="metric-content"></div>`);
  function findMetric(id){return latest[id] || latest.market_environment?.series?.[id] || {};}
  function drawMetric(view="value") {
    const metric=findMetric(activeMetric),state=metricState(metric),points=normalizeHistory(metric.history),model=chartModel(points);
    metricDialog.querySelector("h2").textContent=metricNames[activeMetric] || metric.label || activeMetric;
    metricDialog.querySelectorAll("[data-view]").forEach(button=>{button.setAttribute("aria-pressed",String(button.dataset.view===view));if(button.dataset.view==="chart")button.disabled=!model;});
    const context=`<div class="metric-headline">${escape(valueText(metric))}<span class="data-state ${escape(state)}">${escape(labels[state] || labels.unknown)}</span></div><p class="metric-meta">관측 ${escape(observationText(metric))}${metric.unit?` · 단위 ${escape(unitText(metric.unit))}`:""}</p>${readingHtml(activeMetric,metric)}`;
    const chart=model?`<figure class="metric-chart"><svg viewBox="0 0 560 220" role="img" aria-label="${escape(metricNames[activeMetric] || activeMetric)} 관측 추이"><line x1="42" y1="16" x2="42" y2="188" class="chart-axis"/><line x1="42" y1="188" x2="544" y2="188" class="chart-axis"/><path d="${model.path}" class="chart-line"/></svg><p class="chart-range">그래프 범위 ${model.minimum.toLocaleString("ko-KR",{maximumFractionDigits:2})} — ${model.maximum.toLocaleString("ko-KR",{maximumFractionDigits:2})}</p><figcaption>${escape(points[0].date.slice(0,10))} — ${escape(points.at(-1).date.slice(0,10))} · ${points.length}회 관측${metric.unit?` · 단위 ${escape(unitText(metric.unit))}`:""}</figcaption></figure>`:"";
    const table=points.length?`<div class="metric-history"><table><caption>최근 관측값</caption><thead><tr><th scope="col">관측일</th><th scope="col">값${metric.unit?` (${escape(unitText(metric.unit))})`:""}</th></tr></thead><tbody>${[...points].reverse().slice(0,12).map(p=>`<tr><td>${escape(p.date.slice(0,10))}</td><td>${escape(p.value.toLocaleString("ko-KR",{maximumFractionDigits:2}))}</td></tr>`).join("")}</tbody></table></div>`:"";
    metricDialog.querySelector("#metric-content").innerHTML=context+(view==="chart"?chart:table)+(!model?'<p class="metric-meta">비교 가능한 관측값이 2개 이상 쌓이면 그래프를 볼 수 있습니다.</p>':"")+`<div class="metric-source">${sourceHtml(metric)}${metric.retrieved_at?`<span>수집 ${escape(dateText(metric.retrieved_at))}</span>`:""}</div>`;
  }
  metricDialog.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>drawMetric(button.dataset.view)));
  function showMetric(id,opener){activeMetric=id;drawMetric();openDialog(metricDialog,opener);}
  for(const id of ["vix","qqq","kospi","usdkrw"]){const card=document.querySelector(`#macro-${id}`);if(!card)continue;card.setAttribute("role","button");card.tabIndex=0;card.setAttribute("aria-haspopup","dialog");card.setAttribute("aria-label",`${metricNames[id]} 값과 그래프 보기`);card.addEventListener("click",()=>showMetric(id,card));card.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();showMetric(id,card);}});}
  const overview=document.createElement("details");overview.className="market-overview";overview.id="market-overview";overview.innerHTML='<summary><span>거시·Study 점검</span><span id="market-overview-status">자료 확인 중</span></summary><div id="market-overview-content"></div>';
  document.querySelector(".macro-grid")?.insertAdjacentElement("afterend",overview);
  const notice=document.createElement("div");notice.className="data-notice";notice.id="data-notice";notice.hidden=true;notice.setAttribute("role","status");document.querySelector(".macro-grid")?.insertAdjacentElement("beforebegin",notice);
  function update(data) {
    latest=data || {};let pending=0;
    for(const id of ["vix","qqq","kospi","usdkrw"]){
      const metric=findMetric(id),state=metricState(metric),card=document.querySelector(`#macro-${id}`);if(!card)continue;
      if(number(metric.current ?? metric.price ?? metric.value)===null){
        card.querySelector(".macro-value").textContent="—";
        const sub=card.querySelector(".macro-sub");if(sub)sub.textContent="유효한 관측값 없음";
        card.querySelectorAll(".ma-bar").forEach(bar=>bar.hidden=true);
      } else card.querySelectorAll(".ma-bar").forEach(bar=>bar.hidden=false);
      if(!["fresh","market_closed"].includes(state))pending++;
      let note=card.querySelector(".macro-freshness");if(!note){note=document.createElement("div");note.className="macro-freshness";card.append(note);}
      note.textContent=`${labels[state] || labels.unknown} · ${observationText(metric)}`;card.dataset.dataStatus=state;
      if(!["fresh","market_closed"].includes(state)){const badge=card.querySelector(".macro-badge");if(badge){badge.textContent=labels[state] || labels.unknown;badge.className="macro-badge data-state";}}
    }
    const collectionState={running:"수집 중 · 종목 결과는 직전 완료본입니다.",partial:"일부 수집 실패 · 직전값과 원천 시각을 확인하세요.",failed:"최근 수집 실패 · 직전 완료본을 표시합니다."}[data.collection_status];
    notice.hidden=pending===0&&!collectionState;
    notice.textContent=[collectionState,pending?`시장 지표 ${pending}개는 최신 관측 여부를 확인해야 합니다. 각 카드를 눌러 기준 시각을 확인하세요.`:""].filter(Boolean).join(" ");
    const detect=document.querySelector("#detect-status");if(detect){detect.textContent=collectionState?({running:"수집 진행 중",partial:"일부 수집 확인",failed:"최근 수집 실패"}[data.collection_status]):pending?"자료 확인 필요":"자료 수집 완료";detect.classList.toggle("needs-review",pending>0||!!collectionState);}
    const label=document.querySelector(".updated-label");if(label)label.textContent="마지막 수집";
    const env=data.market_environment || {},series=env.series || {};
    const cycleReport=studyReportMeta(data);
    const body=document.querySelector("#market-overview-content");
    document.querySelector("#market-overview-status").textContent=["passed","complete"].includes(env.quality_status)?"비교·해석 보기":"일부 자료 확인 필요";
    body.innerHTML=`<p class="overview-summary">거시는 보유 기업의 이익 전망과 자금 조달 여건을 점검하는 참고 자료입니다. 각 지표에서 비교 기준과 해석을 확인하세요.</p><div class="environment-series">${Object.entries(series).map(([id,metric])=>{const reading=metricReading(id,metric);return `<button type="button" class="series-button" data-series="${escape(id)}"><span class="series-heading"><span>${escape(metricNames[id] || metric.label || id)}</span><span class="series-value">${escape(valueText(metric))}</span></span><span class="series-comparison">${escape(reading.comparison)}</span><span class="series-reading">${escape(reading.meaning)}</span><small>${escape(labels[metricState(metric)] || labels.unknown)} · ${escape(observationText(metric))}</small><span class="series-more">해석·함께 볼 지표·그래프 ↗</span></button>`;}).join("")}</div><div class="study-status"><h3>Study 근거 점검</h3><p>${escape(data.study_summary?.summary || data.research_summary?.summary || cycleReport.summary)}</p><p class="metric-meta">우호·중립·주의는 산업 환경 점검입니다. 종목을 펼쳐 지지·반대 근거와 다음 확인일을 함께 보세요.</p>${cycleReport.generated_at || data.cycle_generated_at?`<p class="metric-meta">발행 ${escape(dateText(cycleReport.generated_at || data.cycle_generated_at))}</p>`:""}${cycleReport.expires_at?`<p class="metric-meta">유효 기한 ${escape(dateText(cycleReport.expires_at))}</p>`:""}${data.event_sync_status?`<p class="metric-meta">공식 발표 자료: ${escape(labels[data.event_sync_status] || data.event_sync_status)}</p>`:""}</div>`;
    body.querySelectorAll("[data-series]").forEach(button=>button.addEventListener("click",()=>showMetric(button.dataset.series,button)));
    if(metricDialog.open && activeMetric)drawMetric(metricDialog.querySelector('[data-view][aria-pressed="true"]')?.dataset.view || "value");
  }
  return {update,refreshPush,openSettings:()=>{themeToggle.checked=document.documentElement.dataset.theme!=="light";openDialog(settings);void refreshPush();}};
}
