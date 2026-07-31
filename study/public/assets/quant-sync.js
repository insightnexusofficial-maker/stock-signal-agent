(function () {
  function ratingLabel(value) {
    if (!Number.isFinite(value)) return "데이터 대기";
    if (value >= 85) return "실적 매우 강함";
    if (value >= 70) return "실적 강함";
    if (value >= 50) return "실적 보통";
    if (value >= 30) return "실적 약함";
    return "실적 매우 약함";
  }

  function reflectionLabel(value) {
    if (!Number.isFinite(value)) return "데이터 대기";
    if (value >= 85) return "매우 많이 반영";
    if (value >= 70) return "많이 반영";
    if (value >= 50) return "보통 반영";
    if (value >= 30) return "적게 반영";
    return "매우 적게 반영";
  }

  function qualityLabel(quality) {
    const available = Number(quality?.available) || 0;
    const total = Number(quality?.total) || 4;
    const level = { high: "근거 충분", medium: "근거 보통", low: "근거 제한", unavailable: "판단 보류" }[quality?.level] || "검증 대기";
    return `${level} · 근거 ${available}/${total}`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[character]);
  }

  function metric(value, digits = 1, suffix = "") {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(digits)}${suffix}` : "—";
  }

  function isExpired(snapshot) {
    const expires = Date.parse(snapshot?.expires_at || "");
    return !Number.isFinite(expires) || expires <= Date.now();
  }

  function renderPending(container, message) {
    container.className = "gauges quant-sync";
    container.innerHTML = `<div class="rating-card is-pending"><div class="rating-head"><span class="rating-title">기업 상태</span><strong class="rating-score">—</strong></div><div class="rating-meta">${escapeHtml(message)}</div></div>`;
  }

  function ratingCard(title, value, detail, kind, labeler = ratingLabel) {
    const rating = Number(value);
    if (!Number.isFinite(rating)) {
      return `<div class="rating-card is-pending"><div class="rating-head"><span class="rating-title">${escapeHtml(title)}</span><strong class="rating-score">—</strong></div><div class="rating-meta">${escapeHtml(detail || "산정 보류")}</div></div>`;
    }
    const safeRating = Math.max(1, Math.min(100, Math.round(rating)));
    return `<div class="rating-card rating-${kind}">
      <div class="rating-head"><span class="rating-title">${escapeHtml(title)}</span><span class="rating-label">${escapeHtml(labeler(safeRating))}</span></div>
      <div class="rating-number"><strong>${safeRating}</strong><span>/ 100</span></div>
      <div class="rating-track" role="meter" aria-valuemin="1" aria-valuemax="100" aria-valuenow="${safeRating}" aria-label="${escapeHtml(title)} ${safeRating}점"><i style="--rating:${safeRating}%"></i><b aria-hidden="true"></b></div>
      <div class="rating-meta">${escapeHtml(detail)}</div>
    </div>`;
  }

  function renderQuant(container, stock, snapshot) {
    const expired = isExpired(snapshot);
    if (!stock || expired) {
      renderPending(container, expired ? "갱신 주기 초과" : "커버리지 없음");
      return;
    }
    const fundamentals = stock.fundamental_gate || { status: "pending" };
    const valuation = stock.valuation_gate || { status: "pending" };
    if (stock.sayo_alignment === "drift") {
      renderPending(container, "데이터 갱신 대기");
      return;
    }
    const ratings = stock.ratings || {};
    const quality = stock.rating_quality || {};
    const fundamentalDetail = qualityLabel(quality.fundamental);
    const providerNote = quality.price_reflection?.note === "provider_peg_horizon_unavailable" ? " · 산정기간 미공개" : "";
    const reflectionDetail = `${qualityLabel(quality.price_reflection)}${providerNote}`;
    container.className = "gauges quant-sync";
    container.innerHTML = `
      ${ratingCard("실적 체력", ratings.fundamental, fundamentalDetail, "fundamental")}
      ${ratingCard("가격 부담", ratings.price_reflection, reflectionDetail, "price", reflectionLabel)}
      <div class="quant-stamp">${escapeHtml(stock.assessment?.label || "다음 실적 발표 전 기존 판단 유지")} · 실적 체력은 성장·이익·현금·재무·전망, 가격 부담은 현재 이익 대비 가격 수준 · 50점은 중간 · 기준일 ${escapeHtml(stock.data_as_of || "미확인")}</div>`;
  }

  async function loadQuant() {
    const cards = document.querySelectorAll("details.co");
    if (!cards.length) return;
    cards.forEach((card) => {
      const container = card.querySelector(".gauges");
      if (container) renderPending(container, "불러오는 중");
    });
    try {
      const response = await fetch("data/quant-latest.json", { cache: "no-store" });
      if (!response.ok) throw new Error("quant fetch failed");
      const snapshot = await response.json();
      const byTicker = new Map((snapshot.stocks || []).map((stock) => [String(stock.ticker).toUpperCase(), stock]));
      cards.forEach((card) => {
        const container = card.querySelector(".gauges");
        const ticker = card.querySelector(".tick")?.textContent?.trim().toUpperCase();
        const name = card.querySelector(".name")?.textContent?.trim() || "";
        if (!container) return;
        if (name.includes("삼성 파운드리")) {
          renderPending(container, "사업부 단독 정량값 없음");
          return;
        }
        if (!ticker || ticker === "비상장") {
          renderPending(container, "상장 주가 없음");
          return;
        }
        renderQuant(container, byTicker.get(ticker), snapshot);
      });
    } catch (_) {
      cards.forEach((card) => {
        const container = card.querySelector(".gauges");
        if (container) renderPending(container, "데이터 갱신 대기");
      });
    }
  }

  loadQuant();
})();
