import { initializeApp } from "https://www.gstatic.com/firebasejs/10.7.1/firebase-app.js";
import {
  browserSessionPersistence,
  getAuth,
  getIdTokenResult,
  onAuthStateChanged,
  setPersistence,
  signInWithEmailAndPassword,
  signOut,
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js";
import {
  collection,
  deleteDoc,
  doc,
  getDocs,
  getFirestore,
  updateDoc,
} from "https://www.gstatic.com/firebasejs/10.7.1/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyALQUtr9qGDdoqj-Jdwrkw3XQpxBuQ7joQ",
  authDomain: "stock-sayo.firebaseapp.com",
  projectId: "stock-sayo",
  storageBucket: "stock-sayo.firebasestorage.app",
  messagingSenderId: "964666304071",
  appId: "1:964666304071:web:0c806002d44229f71e3362",
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);
const state = { filter: "pending", records: [] };
const $ = (selector) => document.querySelector(selector);

const loginPanel = $("#login-panel");
const dashboard = $("#dashboard");
const loginForm = $("#login-form");
const loginButton = $("#login-button");
const loginMessage = $("#login-message");
const dashboardMessage = $("#dashboard-message");
const userList = $("#user-list");

function setLoginMessage(message) {
  loginMessage.textContent = message || "";
}

function setDashboardMessage(message, isError = false) {
  dashboardMessage.textContent = message || "";
  dashboardMessage.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function showLogin() {
  loginPanel.classList.remove("hidden");
  dashboard.classList.add("hidden");
}

function showDashboard(user) {
  loginPanel.classList.add("hidden");
  dashboard.classList.remove("hidden");
  $("#current-user").textContent = user.email || "관리자";
}

function recordTime(record) {
  if (record.createdAt?.toMillis) return record.createdAt.toMillis();
  const parsed = Date.parse(record.registered_at || "");
  return Number.isNaN(parsed) ? 0 : parsed;
}

function formatTime(record) {
  const value = recordTime(record);
  return value ? new Date(value).toLocaleString("ko-KR") : "등록 시각 없음";
}

function updateStats() {
  const approved = state.records.filter((record) => record.approved === true).length;
  $("#pending-count").textContent = String(state.records.length - approved);
  $("#approved-count").textContent = String(approved);
  $("#total-count").textContent = String(state.records.length);
}

function makeButton(label, className, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function approveRecord(record) {
  setDashboardMessage("승인 처리 중...");
  try {
    await updateDoc(doc(db, "fcm_tokens", record.id), { approved: true });
    await loadUsers();
  } catch (error) {
    console.error("승인 실패", error);
    setDashboardMessage("승인 처리에 실패했습니다.", true);
  }
}

async function deleteRecord(record) {
  if (!window.confirm("이 알림 신청을 삭제하시겠습니까?")) return;
  setDashboardMessage("삭제 처리 중...");
  try {
    await deleteDoc(doc(db, "fcm_tokens", record.id));
    await loadUsers();
  } catch (error) {
    console.error("삭제 실패", error);
    setDashboardMessage("삭제 처리에 실패했습니다.", true);
  }
}

function renderUsers() {
  const filtered = state.records.filter((record) => {
    if (state.filter === "all") return true;
    return state.filter === "approved" ? record.approved === true : record.approved !== true;
  });

  userList.replaceChildren();
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "해당하는 신청이 없습니다.";
    userList.append(empty);
    return;
  }

  filtered.forEach((record) => {
    const row = document.createElement("article");
    row.className = "user-row";

    const info = document.createElement("div");
    const nickname = document.createElement("div");
    nickname.className = "nickname";
    nickname.textContent = String(record.nickname || "익명");
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = formatTime(record);
    info.append(nickname, meta);

    const status = document.createElement("div");
    status.className = record.approved === true ? "status approved" : "status";
    status.textContent = record.approved === true ? "승인됨" : "대기중";

    const actions = document.createElement("div");
    actions.className = "row-actions";
    if (record.approved !== true) {
      actions.append(makeButton("승인", "approve", () => approveRecord(record)));
    }
    actions.append(makeButton("삭제", "danger", () => deleteRecord(record)));
    row.append(info, status, actions);
    userList.append(row);
  });
}

async function loadUsers() {
  setDashboardMessage("목록 불러오는 중...");
  try {
    const snapshot = await getDocs(collection(db, "fcm_tokens"));
    state.records = snapshot.docs
      .map((item) => ({ id: item.id, ...item.data() }))
      .sort((left, right) => recordTime(right) - recordTime(left));
    updateStats();
    renderUsers();
    setDashboardMessage(`최근 갱신 ${new Date().toLocaleTimeString("ko-KR")}`);
  } catch (error) {
    console.error("목록 조회 실패", error);
    state.records = [];
    updateStats();
    renderUsers();
    setDashboardMessage("목록을 불러오지 못했습니다.", true);
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginButton.disabled = true;
  setLoginMessage("");
  try {
    await setPersistence(auth, browserSessionPersistence);
    await signInWithEmailAndPassword(auth, $("#email").value.trim(), $("#password").value);
    loginForm.reset();
  } catch (error) {
    console.error("로그인 실패", error);
    setLoginMessage("로그인 정보가 올바르지 않거나 접근할 수 없습니다.");
  } finally {
    loginButton.disabled = false;
  }
});

$("#refresh-button").addEventListener("click", loadUsers);
$("#logout-button").addEventListener("click", () => signOut(auth));
document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    document.querySelectorAll(".filter").forEach((item) => item.classList.toggle("active", item === button));
    renderUsers();
  });
});

onAuthStateChanged(auth, async (user) => {
  if (!user) {
    showLogin();
    return;
  }
  try {
    const token = await getIdTokenResult(user, true);
    if (token.claims.admin !== true) {
      await signOut(auth);
      setLoginMessage("관리자 권한이 없는 계정입니다.");
      return;
    }
    showDashboard(user);
    await loadUsers();
  } catch (error) {
    console.error("권한 확인 실패", error);
    await signOut(auth);
    setLoginMessage("관리자 권한을 확인하지 못했습니다.");
  }
});
