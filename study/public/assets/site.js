(function () {
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[character]);
  }

  const button = document.querySelector(".mobile-nav-button");
  const nav = document.querySelector(".topnav");
  if (button && nav) {
    function setTopnav(open) {
      nav.classList.toggle("open", open);
      button.setAttribute("aria-expanded", String(open));
    }

    button.addEventListener("click", () => setTopnav(!nav.classList.contains("open")));
    nav.addEventListener("click", (event) => {
      if (event.target.closest("a")) setTopnav(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("open")) setTopnav(false);
    });
  }

  const drawerButton = document.querySelector(".menu-btn");
  const drawer = document.querySelector(".sidebar");
  const overlay = document.querySelector(".overlay");
  const main = document.querySelector(".main");

  function setDrawer(open) {
    if (!drawer || !overlay || !drawerButton) return;
    drawer.classList.toggle("open", open);
    overlay.classList.toggle("show", open);
    drawerButton.setAttribute("aria-expanded", String(open));
    drawerButton.setAttribute("aria-label", open ? "메뉴 닫기" : "메뉴 열기");
    if (main) main.toggleAttribute("inert", open && window.innerWidth <= 920);
    if (open) {
      const firstLink = drawer.querySelector("a");
      if (firstLink && window.innerWidth <= 920) firstLink.focus({ preventScroll: true });
    } else if (document.activeElement && drawer.contains(document.activeElement)) {
      drawerButton.focus({ preventScroll: true });
    }
  }

  if (drawerButton && drawer && overlay) {
    drawerButton.addEventListener("click", () => setDrawer(!drawer.classList.contains("open")));
    overlay.addEventListener("click", () => setDrawer(false));
    drawer.addEventListener("click", (event) => {
      if (event.target.closest("a") && window.innerWidth <= 920) setDrawer(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drawer.classList.contains("open")) setDrawer(false);
    });
    window.addEventListener("resize", () => {
      if (window.innerWidth > 920) setDrawer(false);
    });
  }

  const statusLabel = {
    favorable: "사이클 우호",
    neutral: "사이클 중립",
    caution: "사이클 주의",
    pending: "사이클 중립 · 데이터 갱신 대기",
  };
  const segmentLabel = {
    cloud_capex: "클라우드 설비투자", ai_compute_design: "AI 연산·칩 설계",
    memory_hbm_dram: "HBM·DRAM", memory_nand: "NAND", foundry_logic: "파운드리·로직",
    equipment_materials: "장비·소부장", analog_auto_industrial: "아날로그·자동차·산업용",
    power_infrastructure: "전력·데이터센터 인프라", ai_services: "AI 모델·서비스",
  };
  const pillarLabel = { demand: "수요", inventory: "재고", pricing: "가격", supply: "공급", earnings: "실적" };
  const sourceFamilyLabel = {
    memory_micron_ir: "Micron 공식 실적", industry_association_semi: "SEMI 공식 전망",
    foundry_tsmc_ir: "TSMC 공식 실적", infrastructure_vertiv_ir: "Vertiv 공식 발표",
    fabless_nvidia_ir: "NVIDIA 공식 실적", fabless_amd_ir: "AMD 공식 실적",
    equipment_asml_ir: "ASML 공식 실적", hyperscaler_microsoft_ir: "Microsoft 공식 실적",
    hyperscaler_meta_ir: "Meta 공식 실적", industry_association_wsts: "WSTS 공식 전망",
  };

  function isExpired(report) {
    const expires = Date.parse(report?.expires_at || "");
    return !Number.isFinite(expires) || expires <= Date.now();
  }

  function safeStatus(report, segment) {
    if (isExpired(report) || report?.quality_gate?.status === "insufficient") return "pending";
    return ["favorable", "neutral", "caution"].includes(segment?.status)
      ? segment.status
      : "pending";
  }

  function renderCycleCards(report, target) {
    const segments = Array.isArray(report?.segments) ? report.segments : [];
    if (!segments.length) {
      target.innerHTML = '<div class="notice"><strong>사이클 중립 · 데이터 갱신 대기</strong><br>검증된 최신 리포트를 불러오지 못했습니다.</div>';
      return;
    }
    target.innerHTML = segments.map((segment) => {
      const status = safeStatus(report, segment);
      const reason = status === "pending" ? "유효한 최신 근거를 기다리고 있습니다." : (segment.reason || "검증된 설명이 없습니다.");
      const evidenceCount = Array.isArray(segment.supporting_evidence_ids) ? segment.supporting_evidence_ids.length : 0;
      const contraryCount = Array.isArray(segment.contrary_evidence_ids) ? segment.contrary_evidence_ids.length : 0;
      const evidenceLabel = evidenceCount || contraryCount
        ? `근거 ${evidenceCount} · 반대 ${contraryCount}`
        : "검증 근거 대기";
      return `<article class="segment-card">
        <div class="status-row"><span class="flow-no">${escapeHtml(segment.order || "—")}</span><span class="status-badge status-${status}">${statusLabel[status]}</span></div>
        <h3>${escapeHtml(segment.label || segment.id)}</h3>
        <p>${escapeHtml(reason)}</p>
        <div class="segment-meta">${evidenceLabel} · 신뢰도 ${Math.min(Number(segment.confidence) || 0, 85)} · 3–6개월</div>
      </article>`;
    }).join("");
  }

  function renderCycleSummary(report, target) {
    const segments = Array.isArray(report?.segments) ? report.segments : [];
    if (!segments.length || isExpired(report) || report?.quality_gate?.status === "insufficient") {
      target.innerHTML = '<div class="notice"><strong>사이클 중립 · 데이터 갱신 대기</strong><br>최신 검증 데이터가 없으면 중립으로 표시합니다.</div>';
      return;
    }
    const counts = segments.reduce((acc, segment) => {
      acc[safeStatus(report, segment)] = (acc[safeStatus(report, segment)] || 0) + 1;
      return acc;
    }, {});
    const evidenceCount = Array.isArray(report.evidence) ? report.evidence.length : 0;
    target.innerHTML = `<div class="cycle-summary-card">
      <div>
        <p class="section-kicker">이번 주 사이클</p>
        <h3>영역별 현재 상태</h3>
        <p>우호·주의는 서로 다른 출처와 증거가 충분할 때만 표시하고, 부족하면 중립이 기본값입니다.</p>
      </div>
      <div class="cycle-summary-counts" aria-label="사이클 상태 집계">
        <span><b>${counts.favorable || 0}</b>우호</span>
        <span><b>${counts.neutral || 0}</b>중립</span>
        <span><b>${counts.caution || 0}</b>주의</span>
        <span><b>${evidenceCount}</b>근거</span>
      </div>
    </div>`;
  }

  function renderEvidence(report, target) {
    if (isExpired(report)) {
      target.innerHTML = '<div class="notice"><strong>검증 근거 갱신 대기</strong><br>만료된 근거는 현재 근거로 표시하지 않습니다.</div>';
      return;
    }
    const evidence = Array.isArray(report?.evidence) ? report.evidence : [];
    if (!evidence.length) {
      target.innerHTML = '<div class="notice"><strong>검증 근거 준비 중</strong><br>출처·발행일·산업 영역 검증을 통과한 항목만 여기에 표시됩니다.</div>';
      return;
    }
    target.innerHTML = evidence.map((item) => `<article class="content-card">
      <p class="section-kicker">${escapeHtml(item.segment_label || segmentLabel[item.segment] || item.segment)} · ${escapeHtml(pillarLabel[item.pillar] || item.pillar)}</p>
      <h3>${escapeHtml(item.fact)}</h3>
      <p class="section-copy">${escapeHtml(item.published_at)} · ${escapeHtml(sourceFamilyLabel[item.source_family] || item.source_family)}</p>
      <p><a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">공식 원문 보기</a></p>
    </article>`).join("");
  }

  async function loadCycle() {
    const targets = document.querySelectorAll("[data-cycle-cards]");
    const evidenceTargets = document.querySelectorAll("[data-cycle-evidence]");
    const summaryTargets = document.querySelectorAll("[data-cycle-summary]");
    if (!targets.length && !evidenceTargets.length && !summaryTargets.length) return;
    try {
      const response = await fetch("data/cycle-latest.json", { cache: "no-store" });
      if (!response.ok) throw new Error("cycle fetch failed");
      const report = await response.json();
      targets.forEach((target) => renderCycleCards(report, target));
      evidenceTargets.forEach((target) => renderEvidence(report, target));
      summaryTargets.forEach((target) => renderCycleSummary(report, target));
      document.querySelectorAll("[data-report-stamp]").forEach((node) => {
        node.textContent = isExpired(report)
          ? "사이클 중립 · 데이터 갱신 대기"
          : `갱신 ${report.generated_at || "대기"}`;
      });
    } catch (_) {
      targets.forEach((target) => renderCycleCards(null, target));
      evidenceTargets.forEach((target) => renderEvidence(null, target));
      summaryTargets.forEach((target) => renderCycleSummary(null, target));
    }
  }

  function formatEventTime(item) {
    if (!item?.scheduled_at) {
      return `${escapeHtml(item?.scheduled_date || "일정 확인 중")} · 시각 재확인`;
    }
    const value = new Date(item.scheduled_at);
    if (Number.isNaN(value.getTime())) return escapeHtml(item.scheduled_date || "일정 확인 중");
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(value);
  }

  function renderEventCards(feed, target) {
    const events = Array.isArray(feed?.events) ? feed.events : [];
    if (!events.length) {
      target.innerHTML = '<div class="notice"><strong>확정 일정 대기</strong><br>공식 발표기관이 날짜를 확인한 항목만 표시합니다.</div>';
      return;
    }
    const statusLabel = {
      scheduled: "발표 예정",
      due: "결과 확인 중",
      synced: "공식 결과 동기화",
      overdue: "결과 검증 대기",
    };
    target.innerHTML = events.slice(0, 9).map((item) => `<article class="content-card">
      <div class="status-row">
        <p class="section-kicker">${item.kind === "macro" ? "거시" : "기업"}${item.ticker ? ` · ${escapeHtml(item.ticker)}` : ""}</p>
        <span class="status-badge ${item.sync_status === "synced" ? "status-favorable" : "status-neutral"}">${escapeHtml(
          item.sync_status === "due" && item.result_collection_status === "manual-official-review"
            ? "검증 불가 · 공식 결과 확인 필요"
            : (statusLabel[item.sync_status] || "일정 확인")
        )}</span>
      </div>
      <h3>${escapeHtml(item.name)}</h3>
      <p class="section-copy">${formatEventTime(item)} KST</p>
      <p class="section-copy">${escapeHtml(item.time_note || "공식 발표 시각 기준")}</p>
      <p><a href="${escapeHtml(item.schedule_source_url)}" target="_blank" rel="noopener noreferrer">공식 일정 확인</a></p>
    </article>`).join("");
  }

  function renderEventResults(feed, target) {
    const results = Array.isArray(feed?.recent_results) ? feed.recent_results : [];
    if (!results.length) {
      target.innerHTML = '<div class="notice"><strong>최근 검증 결과 없음</strong><br>발표일이 지나고 공식 결과가 확인되면 이곳에 동기화 상태가 표시됩니다.</div>';
      return;
    }
    target.innerHTML = `<h3>최근 공식 발표 결과</h3><div class="grid grid-2">${results.map((item) => `<article class="content-card">
      <p class="section-kicker">${escapeHtml(item.event_name || item.event_id)}</p>
      <h3>${escapeHtml(item.summary || "공식 결과 확인")}</h3>
      <p class="section-copy">${escapeHtml(item.reference_period || "")} · ${escapeHtml(item.retrieved_at || "")}</p>
      ${item.shock?.is_shock ? '<p class="status-badge status-caution">객관적 쇼크 기준 충족 · 07:00 알림 대기</p>' : ""}
      <p><a href="${escapeHtml((item.source_urls || [])[0] || "#")}" target="_blank" rel="noopener noreferrer">공식 결과 보기</a></p>
    </article>`).join("")}</div>`;
  }

  async function loadEvents() {
    const cardTargets = document.querySelectorAll("[data-event-cards]");
    const resultTargets = document.querySelectorAll("[data-event-results]");
    if (!cardTargets.length && !resultTargets.length) return;
    try {
      const response = await fetch("data/event-latest.json", { cache: "no-store" });
      if (!response.ok) throw new Error("event fetch failed");
      const feed = await response.json();
      cardTargets.forEach((target) => renderEventCards(feed, target));
      resultTargets.forEach((target) => renderEventResults(feed, target));
    } catch (_) {
      cardTargets.forEach((target) => renderEventCards(null, target));
      resultTargets.forEach((target) => renderEventResults(null, target));
    }
  }

  loadCycle();
  loadEvents();
})();
