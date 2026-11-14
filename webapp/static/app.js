(() => {
  "use strict";

  const tg = window.Telegram?.WebApp;
  const devUser = new URLSearchParams(location.search).get("dev_user_id");
  const view = document.getElementById("view");
  const modal = document.getElementById("modal");
  const modalCard = document.getElementById("modal-card");
  const tabbar = document.getElementById("tabbar");
  const toastEl = document.getElementById("toast");
  const teacherTab = tabbar.querySelector('[data-tab="teacher"]');

  const state = {
    tab: "home",
    me: null,
    tasks: [],
    map: null,
    shop: null,
    boss: null,
    loading: true,
    error: null,
    teacher: {
      section: "overview",
      overview: null,
      students: [],
      topics: [],
      allTopics: [],
      topicPage: 0,
      topicPages: 1,
      topicTotal: 0,
      topicQuery: "",
      reviews: [],
      reviewKind: "pending",
      loading: false,
      loaded: false,
      modal: null,
    },
  };

  let toastTimer = null;

  // ─────────────────────────── Утилиты ───────────────────────────

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function pct(value, total) {
    if (!total) return 0;
    return Math.max(0, Math.min(100, Math.round((value / total) * 100)));
  }

  function haptic(kind) {
    try {
      if (!tg?.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (kind === "error") tg.HapticFeedback.notificationOccurred("error");
      else tg.HapticFeedback.impactOccurred("light");
    } catch (_) {}
  }

  function toast(message, kind = "") {
    toastEl.textContent = message;
    toastEl.className = "toast show " + kind;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.className = "toast " + kind;
    }, 2600);
  }

  function applyTheme() {
    if (!tg) return;
    document.body.dataset.theme = tg.colorScheme === "light" ? "light" : "dark";
    try {
      tg.setHeaderColor(tg.themeParams?.bg_color || (tg.colorScheme === "light" ? "#ffffff" : "#0b0f1a"));
      tg.setBackgroundColor(tg.themeParams?.bg_color || (tg.colorScheme === "light" ? "#ffffff" : "#0b0f1a"));
    } catch (_) {}
  }

  function headers() {
    const result = { "Content-Type": "application/json" };
    if (tg?.initData) result["X-Telegram-Init-Data"] = tg.initData;
    if (devUser) result["X-Dev-User-Id"] = devUser;
    return result;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { ...headers(), ...(options.headers || {}) },
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {}
    if (!response.ok) {
      const error = new Error(data.detail || "Что-то пошло не так");
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function renderMath(element) {
    if (!element || !window.renderMathInElement) return;
    try {
      window.renderMathInElement(element, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$", right: "$", display: false },
          { left: "\\(", right: "\\)", display: false },
          { left: "\\[", right: "\\]", display: true },
        ],
        throwOnError: false,
        strict: false,
      });
    } catch (_) {}
  }

  function difficultyLabel(value) {
    return { easy: "лёгкое", medium: "среднее", hard: "сложное" }[value] || value;
  }

  function statusBadge(status) {
    const map = {
      correct: '<span class="badge correct">✅ верно</span>',
      wrong: '<span class="badge wrong">❌ ошибка</span>',
      review: '<span class="badge review">⏳ на проверке</span>',
      pending: '<span class="badge pending">✏️ ждёт ответа</span>',
    };
    return map[status] || "";
  }

  // ─────────────────────────── Загрузка ───────────────────────────

  async function loadAll() {
    state.loading = true;
    state.error = null;
    render();
    try {
      const [me, tasks, map, shop] = await Promise.all([
        api("/api/me"),
        api("/api/tasks"),
        api("/api/map"),
        api("/api/shop"),
      ]);
      state.me = me;
      state.tasks = tasks.tasks || [];
      state.map = map;
      state.shop = shop;
    } catch (error) {
      state.error = error;
    }
    state.loading = false;
    render();
  }

  async function refreshCore() {
    const [me, tasks] = await Promise.all([api("/api/me"), api("/api/tasks")]);
    state.me = me;
    state.tasks = tasks.tasks || [];
  }

  // ─────────────────────────── Рендер ───────────────────────────

  function render() {
    document.getElementById("boot").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");

    if (state.loading) {
      view.innerHTML = skeletonHtml();
      return;
    }
    if (state.error) {
      view.innerHTML = errorHtml(state.error);
      return;
    }

    updateChips();
    updateTabs();

    if (state.tab === "home") view.innerHTML = homeHtml();
    else if (state.tab === "tasks") view.innerHTML = tasksHtml();
    else if (state.tab === "map") view.innerHTML = mapHtml();
    else if (state.tab === "shop") view.innerHTML = shopHtml();
    else if (state.tab === "teacher") view.innerHTML = teacherHtml();
    else view.innerHTML = heroHtml();

    renderMath(view);
  }

  function updateChips() {
    const user = state.me?.user;
    document.getElementById("chip-coins").textContent = "🪙 " + (user?.coins ?? 0);
    document.getElementById("chip-streak").textContent = "🔥 " + (user?.streak ?? 0);
  }

  function updateTabs() {
    const isTeacher = Boolean(state.me?.is_teacher);
    if (teacherTab) teacherTab.classList.toggle("hidden", !isTeacher);
    tabbar.style.gridTemplateColumns = `repeat(${isTeacher ? 6 : 5}, 1fr)`;
    tabbar.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.tab === state.tab);
    });
  }

  function skeletonHtml() {
    return `
      <div class="skeleton" style="height:180px"></div>
      <div class="skeleton"></div>
      <div class="skeleton"></div>
      <div class="skeleton"></div>
    `;
  }

  function errorHtml(error) {
    const needStart = error?.status === 404;
    const noAuth = error?.status === 401;
    const title = needStart ? "Ты ещё не зарегистрирован" : noAuth ? "Нужен Telegram" : "Не удалось загрузить";
    const text = needStart
      ? "Вернись в чат с ботом и нажми /start. После этого открой приложение снова."
      : noAuth
        ? "Открой приложение через кнопку в боте — так Telegram подтвердит, кто ты."
        : (error?.message || "Проверь соединение и попробуй ещё раз.");
    return `
      <div class="error-screen">
        <div class="emoji">${needStart ? "👋" : "⚠️"}</div>
        <h2>${esc(title)}</h2>
        <p>${esc(text)}</p>
        <div class="btn-row">
          <button class="btn btn-primary" data-action="retry">Повторить</button>
          <button class="btn btn-ghost" data-action="close-app">Закрыть</button>
        </div>
      </div>
    `;
  }

  function heroCardHtml(compact = false) {
    const user = state.me.user;
    const topic = state.me.topic;
    const xpPct = pct(user.xp_in_level, user.xp_for_level);
    const initial = (user.username || "?").trim().charAt(0).toUpperCase();
    return `
      <section class="card hero-card">
        <div class="hero-top">
          <div class="avatar">${esc(initial)}</div>
          <div>
            <div class="hero-name">${esc(user.username || "Ученик")}</div>
            <div class="hero-rank">${esc(user.rank)} · уровень ${user.level}</div>
          </div>
          <div class="level-badge">⭐ ${user.level}</div>
        </div>
        <div class="xp-line"><span>XP</span><span>${user.xp_in_level} / ${user.xp_for_level}</span></div>
        <div class="bar"><i style="width:${xpPct}%"></i></div>
        <div class="hero-stats">
          <div class="stat"><b>${user.coins}</b><span>монеты</span></div>
          <div class="stat"><b>${user.streak}</b><span>дней подряд</span></div>
          <div class="stat"><b>${user.xp}</b><span>всего XP</span></div>
        </div>
        ${topic ? `
          <div class="topic-line">
            <span>${esc(topic.emoji || "📚")}</span>
            <div class="grow">
              <b>${esc(topic.title)}</b>
              <small>мастерство ${topic.mastery}%</small>
            </div>
            <span class="badge">${esc(difficultyLabel(topic.difficulty))}</span>
          </div>
        ` : `
          <div class="topic-line">
            <span>🗺</span>
            <div class="grow"><b>Тема пока не назначена</b><small>попроси учителя выбрать тему</small></div>
          </div>
        `}
      </section>
    `;
  }

  function questHtml() {
    const quest = state.me.quest;
    const progress = pct(quest.progress, quest.goal);
    return `
      <section class="card quest-card">
        <div class="quest-head">
          <div>
            <div class="quest-title">🎯 ${esc(quest.title)}</div>
            <div class="quest-desc">${esc(quest.description)}</div>
          </div>
          <div class="quest-reward">+${quest.reward_xp} XP<br>+${quest.reward_coins} 🪙</div>
        </div>
        <div class="quest-progress">
          <div class="xp-line"><span>Прогресс</span><span>${quest.progress} / ${quest.goal}</span></div>
          <div class="bar ${quest.completed ? "good" : ""}"><i style="width:${progress}%"></i></div>
        </div>
      </section>
    `;
  }

  function miniTaskHtml(task) {
    return `
      <div class="card task-card" data-action="go-tasks" style="cursor:pointer">
        <div class="task-head">
          <span class="badge ${esc(task.difficulty)}">${esc(difficultyLabel(task.difficulty))}</span>
          <span class="badge">${esc(task.skill_emoji)} ${esc(task.skill_title)}</span>
          ${statusBadge(task.status)}
        </div>
        <div class="task-text">${esc(task.text)}</div>
      </div>
    `;
  }

  function bossCardHtml() {
    const active = state.me?.boss_active;
    return `
      <div class="boss-card" data-action="open-boss">
        <div class="boss-emoji">🏰</div>
        <div class="boss-info">
          <b>Босс темы</b>
          <small>${active ? "Бой уже идёт — продолжить" : "5 удачных ответов ломают босса"}</small>
        </div>
        <div class="boss-meta">${active ? "⚔️ в бой" : "начать"}</div>
      </div>
    `;
  }

  function homeHtml() {
    const today = state.me.today;
    const tasksPreview = state.tasks.slice(0, 3);
    const donePct = pct(today.completed, today.total || 3);
    return `
      ${heroCardHtml()}
      <div class="section-title">Сегодня <span class="link" data-action="go-tasks">все задания →</span></div>
      ${questHtml()}
      <div class="card" style="margin-top:12px">
        <div class="xp-line"><span>Заданий закрыто</span><span>${today.completed} / ${today.total || 3}</span></div>
        <div class="bar ${donePct >= 100 ? "good" : ""}"><i style="width:${donePct}%"></i></div>
        ${today.debt ? `<div class="small muted mt8">⚠️ Долг за прошлые дни: ${today.debt}</div>` : ""}
      </div>
      <div class="section-title">Твои задания</div>
      ${tasksPreview.length ? tasksPreview.map(miniTaskHtml).join("") : `
        <div class="empty">
          <div class="emoji">📭</div>
          <h3>Заданий пока нет</h3>
          <p>Можно получить задания прямо сейчас.</p>
          <button class="btn btn-primary" data-action="request-tasks">Получить задания</button>
        </div>
      `}
      <div class="section-title">Испытание</div>
      ${bossCardHtml()}
    `;
  }

  function tasksHtml() {
    const tasks = state.tasks;
    const debt = tasks.filter((task) => task.is_debt).length;
    const header = `
      <div class="section-title">
        Задания на сегодня
        <span class="muted small">${tasks.length}${debt ? ` · долг ${debt}` : ""}</span>
      </div>
    `;
    if (!tasks.length) {
      return header + `
        <div class="empty">
          <div class="emoji">📭</div>
          <h3>На сегодня заданий нет</h3>
          <p>Получи три задания и начни зарабатывать XP.</p>
          <button class="btn btn-primary" data-action="request-tasks">Получить задания</button>
        </div>
      `;
    }
    return header + tasks.map(taskCardHtml).join("");
  }

  function taskCardHtml(task) {
    const isPending = task.status === "pending";
    const hint = task.hint ? `<div class="task-hint">💡 ${esc(task.hint)}</div>` : "";
    let statusLine = "";
    if (task.status === "correct") {
      statusLine = `<div class="status-line correct">✅ Верно! Задание закрыто.</div>`;
    } else if (task.status === "wrong") {
      statusLine = `
        <div class="status-line wrong">❌ Ошибка. Попытка была одна.</div>
        <div class="task-answer-box">Правильный ответ: <b>${esc(task.correct_answer || "—")}</b></div>
      `;
    } else if (task.status === "review") {
      statusLine = `<div class="status-line review">⏳ Ответ отправлен учителю на проверку.</div>`;
    }

    const actions = isPending ? `
      <div class="answer-row">
        <input class="input answer-input" data-input="${task.id}" inputmode="text" autocomplete="off" placeholder="Твой ответ">
        <button class="btn btn-primary" data-action="answer" data-id="${task.id}">Ответить</button>
      </div>
      ${task.used_hint ? "" : `<div class="btn-row"><button class="btn btn-soft" data-action="hint" data-id="${task.id}">💡 Подсказка</button></div>`}
    ` : "";

    return `
      <article class="card task-card">
        <div class="task-head">
          <span class="badge ${esc(task.difficulty)}">${esc(difficultyLabel(task.difficulty))}</span>
          <span class="badge">${esc(task.skill_emoji)} ${esc(task.skill_title)}</span>
          ${task.is_debt ? '<span class="badge debt">долг</span>' : ""}
          ${statusBadge(task.status)}
        </div>
        <div class="task-text">${esc(task.text)}</div>
        ${hint}
        ${actions}
        ${statusLine}
      </article>
    `;
  }

  function mapRowsHtml(rows) {
    if (!rows.length) {
      return `<div class="empty"><div class="emoji">🗺</div><h3>Ничего не найдено</h3><p>Попробуй другой запрос.</p></div>`;
    }
    return rows.map((row) => {
      const mark = { current: "📍", mastered: "✅", in_progress: "🔸", available: "🌫" }[row.status] || "🌫";
      return `
        <div class="map-row ${row.status === "current" ? "current" : ""}">
          <div class="map-emoji">${esc(row.emoji || "📚")}</div>
          <div class="map-info">
            <div class="map-title">${esc(row.title)}</div>
            <div class="map-sub">
              <div class="bar ${row.mastery >= 70 ? "good" : ""}"><i style="width:${row.mastery}%"></i></div>
              <span class="map-pct">${row.mastery}%</span>
            </div>
          </div>
          <div class="map-mark">${mark}</div>
        </div>
      `;
    }).join("");
  }

  function mapHtml() {
    const rows = state.map?.rows || [];
    const mastered = rows.filter((row) => row.status === "mastered").length;
    return `
      <div class="section-title">Карта знаний <span class="muted small">${rows.length} тем · ${mastered} пройдено</span></div>
      <input class="input map-search" id="map-search" placeholder="Поиск темы…" autocomplete="off">
      <div id="map-list">${mapRowsHtml(rows)}</div>
    `;
  }

  function shopHtml() {
    const items = state.shop?.items || [];
    const coins = state.me?.user?.coins ?? state.shop?.coins ?? 0;
    return `
      <div class="section-title">Магазин <span class="muted small">🪙 ${coins}</span></div>
      ${items.map((item) => `
        <div class="shop-item">
          <div class="shop-emoji">${esc(item.emoji)}</div>
          <div class="shop-info">
            <b>${esc(item.title)}</b>
            <small>${esc(item.description)}</small>
            ${item.quantity ? `<small class="owned">У тебя: ${item.quantity}</small>` : ""}
          </div>
          <div class="shop-buy">
            <div class="price">${item.price} 🪙</div>
            <button class="btn btn-primary btn-sm" data-action="buy" data-code="${esc(item.code)}">Купить</button>
          </div>
        </div>
      `).join("")}
      <div class="card mt12 small muted">Подсказку можно купить здесь или получить за монеты прямо в задании.</div>
    `;
  }

  function heroHtml() {
    const skills = state.me.skills || [];
    const achievements = state.me.achievements || [];
    return `
      ${heroCardHtml()}
      <div class="section-title">Навыки</div>
      <div class="card skills">
        ${skills.map((skill) => {
          const progress = Math.min(100, Math.round((skill.xp % 100)));
          return `
            <div class="skill-row">
              <div class="skill-head"><b>${esc(skill.emoji)} ${esc(skill.title)}</b><span>ур. ${skill.level} · ${skill.xp} XP</span></div>
              <div class="bar"><i style="width:${progress}%"></i></div>
            </div>
          `;
        }).join("")}
      </div>
      <div class="section-title">Достижения</div>
      ${achievements.length ? achievements.map((achievement) => `
        <div class="achievement">
          <div class="ico">🏆</div>
          <div><b>${esc(achievement.title)}</b><small>${esc(achievement.description)}</small></div>
        </div>
      `).join("") : `
        <div class="empty">
          <div class="emoji">🏆</div>
          <h3>Пока пусто</h3>
          <p>Решай задания — достижения появятся сами.</p>
        </div>
      `}
      <div class="section-title">Квест дня</div>
      ${questHtml()}
    `;
  }

  // ─────────────────────────── Учительская панель ───────────────────────────

  function teacherStat(label, value, cls = "") {
    return `<div class="teacher-stat ${cls}"><b>${value}</b><span>${esc(label)}</span></div>`;
  }

  function teacherPager(page, pages, action, extra = "") {
    if (!pages || pages <= 1) return "";
    return `
      <div class="pager">
        <button class="btn btn-soft btn-sm" data-action="${action}" data-page="${page - 1}" ${extra} ${page <= 0 ? "disabled" : ""}>← Назад</button>
        <span>${page + 1} / ${pages}</span>
        <button class="btn btn-soft btn-sm" data-action="${action}" data-page="${page + 1}" ${extra} ${page >= pages - 1 ? "disabled" : ""}>Вперёд →</button>
      </div>
    `;
  }

  function teacherNavHtml() {
    const pending = state.teacher.overview?.pending_review;
    const items = [
      ["overview", "📊 Обзор"],
      ["reviews", `📨 Проверка${pending ? ` (${pending})` : ""}`],
      ["students", "👨‍🎓 Ученики"],
      ["topics", "📚 Темы"],
    ];
    return `<div class="segmented">${items.map(([code, label]) => `
      <button class="${state.teacher.section === code ? "active" : ""}" data-action="teacher-section" data-section="${code}">${label}</button>
    `).join("")}</div>`;
  }

  function teacherHtml() {
    if (!state.me?.is_teacher) {
      return `
        <div class="empty">
          <div class="emoji">🔒</div>
          <h3>Только для учителя</h3>
          <p>Этот раздел доступен аккаунтам из ADMIN_IDS.</p>
        </div>
      `;
    }
    let body = "";
    if (state.teacher.loading) {
      body = `<div class="skeleton" style="height:90px"></div><div class="skeleton"></div><div class="skeleton"></div>`;
    } else if (state.teacher.section === "students") {
      body = teacherStudentsHtml();
    } else if (state.teacher.section === "topics") {
      body = teacherTopicsHtml();
    } else if (state.teacher.section === "reviews") {
      body = teacherReviewsHtml();
    } else {
      body = teacherOverviewHtml();
    }
    return `${teacherNavHtml()}${body}`;
  }

  function teacherOverviewHtml() {
    const o = state.teacher.overview;
    if (!o) return `<div class="skeleton" style="height:120px"></div><div class="skeleton"></div>`;
    const active = o.active_topic;
    return `
      <div class="section-title">Учительская <span class="link" data-action="teacher-refresh">обновить</span></div>
      <div class="teacher-stats">
        ${teacherStat("учеников", o.students)}
        ${teacherStat("на проверке", o.pending_review, o.pending_review ? "warn" : "")}
        ${teacherStat("ошибок", o.wrong_total, o.wrong_total ? "bad" : "")}
        ${teacherStat("заданий в базе", o.tasks)}
      </div>
      <div class="card">
        <div class="xp-line"><span>Сегодня закрыто</span><span>${o.today_completed} / ${o.today_total}</span></div>
        <div class="bar ${o.today_total && o.today_completed >= o.today_total ? "good" : ""}"><i style="width:${pct(o.today_completed, o.today_total || 1)}%"></i></div>
      </div>
      <div class="teacher-card mt12">
        <h3>${active ? "✅ Активная тема" : "Активная тема не выбрана"}</h3>
        <p>${active ? esc(active.title) : "Выбери тему во вкладке «Темы»."}</p>
      </div>
      <div class="section-title">Быстрые действия</div>
      <div class="grid-2">
        <button class="btn btn-primary" data-action="teacher-section" data-section="reviews">📨 Проверить ответы</button>
        <button class="btn btn-soft" data-action="teacher-section" data-section="students">👨‍🎓 Ученики</button>
        <button class="btn btn-soft" data-action="teacher-section" data-section="topics">📚 Темы</button>
        <button class="btn btn-soft" data-action="teacher-assign-all">📤 Выдать задания всем</button>
      </div>
      <div class="section-title">База</div>
      <div class="grid-2">
        <button class="btn btn-ghost" data-action="teacher-import">📥 Импорт заданий</button>
        <button class="btn btn-ghost" data-action="teacher-seed">🌱 Дополнить базу</button>
      </div>
      <div class="teacher-hint mt12">Импорт и наполнение базы только добавляют недостающие темы и задания. Существующие ученики, ответы и прогресс не затрагиваются.</div>
    `;
  }

  function teacherStudentsHtml() {
    const items = state.teacher.students;
    return `
      <div class="section-title">Ученики <span class="muted small">${items.length}</span></div>
      ${items.length ? items.map((student) => `
        <button class="teacher-row" data-action="teacher-student" data-id="${student.id}">
          <div class="ico">${student.level >= 5 ? "🧙" : "👤"}</div>
          <div class="grow">
            <b>${esc(student.username ? "@" + student.username : "id:" + student.id)}</b>
            <small>${esc(student.topic_title || "тема не назначена")} · ${esc(difficultyLabel(student.difficulty))}</small>
            <small>${student.today_completed}/${student.today_total} сегодня${student.debt ? ` · долг ${student.debt}` : ""}${student.pending ? ` · на проверке ${student.pending}` : ""}</small>
          </div>
          <div class="right"><b>Lv.${student.level}</b><small>${student.xp} XP</small></div>
        </button>
      `).join("") : `
        <div class="empty">
          <div class="emoji">👨‍🎓</div>
          <h3>Учеников пока нет</h3>
          <p>Они появятся здесь после /start в боте.</p>
        </div>
      `}
    `;
  }

  function teacherTopicsHtml() {
    const t = state.teacher;
    return `
      <div class="section-title">Темы <span class="muted small">${t.topicTotal || ""}</span></div>
      <div class="teacher-toolbar">
        <input class="input" id="teacher-topic-query" placeholder="Поиск темы…" value="${esc(t.topicQuery)}" autocomplete="off">
        <button class="btn btn-soft" data-action="teacher-topic-search">Найти</button>
      </div>
      <div class="grid-2">
        <button class="btn btn-primary" data-action="teacher-new-topic">➕ Новая тема</button>
        <button class="btn btn-ghost" data-action="teacher-seed">🌱 Дополнить базу</button>
      </div>
      <div class="mt12">
        ${t.topics.length ? t.topics.map((topic) => `
          <button class="teacher-row" data-action="teacher-topic" data-id="${topic.id}">
            <div class="ico">${esc(topic.emoji || "📚")}</div>
            <div class="grow">
              <b>${esc(topic.title)}</b>
              <small>${topic.tasks_count} заданий${topic.is_active ? " · активная" : ""}</small>
            </div>
            <div class="right">${topic.is_active ? '<span class="teacher-tag active">✅</span>' : ""}</div>
          </button>
        `).join("") : `
          <div class="empty">
            <div class="emoji">📚</div>
            <h3>Тем не найдено</h3>
            <p>Измени запрос или создай новую тему.</p>
          </div>
        `}
      </div>
      ${teacherPager(t.topicPage, t.topicPages, "teacher-topic-page")}
    `;
  }

  function teacherReviewsHtml() {
    const t = state.teacher;
    const kinds = [
      ["pending", "⏳ На проверке"],
      ["wrong", "❌ Ошибки"],
      ["all", "Все"],
    ];
    return `
      <div class="section-title">Проверка ответов</div>
      <div class="segmented">
        ${kinds.map(([code, label]) => `
          <button class="${t.reviewKind === code ? "active" : ""}" data-action="teacher-review-kind" data-kind="${code}">${label}</button>
        `).join("")}
      </div>
      ${t.reviews.length ? t.reviews.map((review) => `
        <button class="teacher-row" data-action="teacher-review" data-id="${review.id}">
          <div class="ico">${review.status === "review" ? "📨" : "❌"}</div>
          <div class="grow">
            <b>${esc(review.username ? "@" + review.username : "id:" + review.student_id)}</b>
            <small>${esc((review.task_text || "").slice(0, 90))}</small>
            <small>${review.answered_at ? esc(review.answered_at.slice(0, 16).replace("T", " ")) : "—"}</small>
          </div>
          <div class="right">${review.status === "review"
            ? '<span class="teacher-tag warn">проверить</span>'
            : '<span class="teacher-tag bad">ошибка</span>'}</div>
        </button>
      `).join("") : `
        <div class="empty">
          <div class="emoji">🎉</div>
          <h3>Здесь пусто</h3>
          <p>Ответы, которым нужна проверка, появятся тут.</p>
        </div>
      `}
    `;
  }

  function teacherModalHead(title, sub = "") {
    return `
      <div class="modal-head">
        <div>
          <div class="modal-title">${title}</div>
          ${sub ? `<div class="modal-sub">${esc(sub)}</div>` : ""}
        </div>
        <button class="icon-btn" data-action="close-modal">✕</button>
      </div>
    `;
  }

  async function loadTeacherSection(section) {
    if (!state.me?.is_teacher) return;
    const t = state.teacher;
    t.section = section || "overview";
    t.loading = true;
    render();
    try {
      if (t.section === "overview") {
        t.overview = await api("/api/teacher/overview");
      } else if (t.section === "students") {
        const data = await api("/api/teacher/students");
        t.students = data.students || [];
      } else if (t.section === "topics") {
        const data = await api(`/api/teacher/topics?query=${encodeURIComponent(t.topicQuery)}&page=${t.topicPage}&per_page=30`);
        t.topics = data.items || [];
        t.topicPage = data.page;
        t.topicPages = data.pages;
        t.topicTotal = data.total;
      } else if (t.section === "reviews") {
        const data = await api(`/api/teacher/reviews?kind=${t.reviewKind}`);
        t.reviews = data.items || [];
      }
      t.loaded = true;
    } catch (error) {
      toast(error.message, "bad");
    }
    t.loading = false;
    if (state.tab === "teacher") render();
  }

  async function openTeacherStudent(id) {
    try {
      const topicsPromise = state.teacher.allTopics.length
        ? Promise.resolve({ items: state.teacher.allTopics })
        : api("/api/teacher/topics?per_page=500");
      const [detail, topics] = await Promise.all([
        api(`/api/teacher/students/${id}`),
        topicsPromise,
      ]);
      state.teacher.allTopics = topics.items || [];
      state.teacher.modal = { type: "student", detail };
      renderTeacherStudentModal();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  function renderTeacherStudentModal() {
    const data = state.teacher.modal;
    if (!data || data.type !== "student") return;
    const { student, recent } = data.detail;
    const options = state.teacher.allTopics.map((topic) => `
      <option value="${topic.id}" ${topic.id === student.topic_id ? "selected" : ""}>${esc(topic.title)}</option>
    `).join("");
    const difficulties = ["easy", "medium", "hard"].map((code) => `
      <option value="${code}" ${code === student.difficulty ? "selected" : ""}>${esc(difficultyLabel(code))}</option>
    `).join("");
    const name = student.username ? "@" + student.username : "id:" + student.id;
    modalCard.innerHTML = `
      ${teacherModalHead("👤 " + esc(name), `${student.rank} · уровень ${student.level}`)}
      <div class="teacher-stats">
        ${teacherStat("верно", student.correct, "good")}
        ${teacherStat("ошибок", student.wrong, "bad")}
        ${teacherStat("на проверке", student.pending, "warn")}
        ${teacherStat("долг", student.debt)}
      </div>
      <div class="card">
        <div class="xp-line"><span>XP</span><span>${student.xp_in_level} / ${student.xp_for_level}</span></div>
        <div class="bar"><i style="width:${pct(student.xp_in_level, student.xp_for_level)}%"></i></div>
        <div class="xp-line mt8"><span>Сегодня</span><span>${student.today_completed} / ${student.today_total}</span></div>
        <div class="bar good"><i style="width:${pct(student.today_completed, student.today_total || 1)}%"></i></div>
        <div class="small muted mt8">Тема: ${esc(student.topic_title || "не назначена")}</div>
      </div>
      <div class="section-title">Занятие</div>
      <div class="card teacher-form">
        <label>Тема</label>
        <select class="select" id="teacher-topic-select">${options || '<option value="">Нет тем</option>'}</select>
        <label>Сложность</label>
        <select class="select" id="teacher-difficulty-select">${difficulties}</select>
        <button class="btn btn-primary" data-action="teacher-save-lesson" data-id="${student.id}">Сохранить тему</button>
      </div>
      <div class="grid-2 mt12">
        <button class="btn btn-soft" data-action="teacher-assign" data-id="${student.id}">📤 Выдать задания</button>
        <button class="btn btn-soft" data-action="teacher-individual" data-id="${student.id}">✏️ Индивидуальное</button>
      </div>
      <div class="section-title">Последние задания</div>
      ${recent.length ? recent.map((item) => `
        <div class="teacher-task-row">
          <div class="task-head">
            ${statusBadge(item.status)}
            <span class="teacher-tag">${esc(item.assigned_date)}</span>
          </div>
          <div class="txt">${esc(item.task_text)}</div>
          ${item.student_answer ? `<div class="small muted mt8">Ответ: ${esc(item.student_answer)}</div>` : ""}
          ${item.correct_answer ? `<div class="ans">Правильно: ${esc(item.correct_answer)}</div>` : ""}
        </div>
      `).join("") : `<div class="teacher-hint">Пока нет выполненных заданий.</div>`}
    `;
    showModal();
    renderMath(modalCard);
  }

  async function saveTeacherLesson(id, button) {
    const topicId = Number(document.getElementById("teacher-topic-select")?.value);
    const difficulty = document.getElementById("teacher-difficulty-select")?.value || "medium";
    if (!topicId) {
      toast("Выбери тему", "bad");
      return;
    }
    button.disabled = true;
    try {
      await api(`/api/teacher/students/${id}/lesson`, {
        method: "POST",
        body: JSON.stringify({ topic_id: topicId, difficulty }),
      });
      state.teacher.overview = null;
      toast("Тема назначена", "good");
      haptic("success");
      await openTeacherStudent(id);
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function assignTeacherTasks(id, button) {
    button.disabled = true;
    try {
      const result = await api(`/api/teacher/students/${id}/assign`, { method: "POST" });
      state.teacher.overview = null;
      toast(result.assigned ? `Выдано заданий: ${result.assigned}` : "На сегодня уже всё выдано", "good");
      haptic("success");
      await openTeacherStudent(id);
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  function openTeacherIndividual(id) {
    state.teacher.modal = { type: "individual", studentId: id };
    renderTeacherIndividualModal();
  }

  function renderTeacherIndividualModal() {
    const data = state.teacher.modal;
    if (!data || data.type !== "individual") return;
    const difficulties = ["easy", "medium", "hard"].map((code) => `
      <option value="${code}" ${code === "medium" ? "selected" : ""}>${esc(difficultyLabel(code))}</option>
    `).join("");
    modalCard.innerHTML = `
      ${teacherModalHead("✏️ Индивидуальное задание", "Только для этого ученика")}
      <div class="teacher-form">
        <label>Текст задания</label>
        <textarea class="textarea" id="teacher-individual-text" placeholder="Например: Реши уравнение $x^2-5x+6=0$"></textarea>
        <label>Ответ (необязательно)</label>
        <input class="input" id="teacher-individual-answer" placeholder="2, 3" autocomplete="off">
        <label>Сложность</label>
        <select class="select" id="teacher-individual-difficulty">${difficulties}</select>
        <button class="btn btn-primary" data-action="teacher-create-individual" data-id="${data.studentId}">Создать задание</button>
      </div>
    `;
    showModal();
    renderMath(modalCard);
  }

  async function createTeacherIndividual(id, button) {
    const text = document.getElementById("teacher-individual-text")?.value.trim() || "";
    const answer = document.getElementById("teacher-individual-answer")?.value.trim() || "";
    const difficulty = document.getElementById("teacher-individual-difficulty")?.value || "medium";
    if (!text) {
      toast("Напиши текст задания", "bad");
      return;
    }
    button.disabled = true;
    try {
      await api(`/api/teacher/students/${id}/individual`, {
        method: "POST",
        body: JSON.stringify({ text, answer: answer || null, difficulty }),
      });
      toast("Индивидуальное задание создано", "good");
      haptic("success");
      await openTeacherStudent(id);
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function openTeacherTopic(id, page = 0) {
    try {
      const data = await api(`/api/teacher/topics/${id}/tasks?page=${page}&per_page=30`);
      state.teacher.modal = { type: "topic", data };
      renderTeacherTopicModal();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  function renderTeacherTopicModal() {
    const data = state.teacher.modal;
    if (!data || data.type !== "topic") return;
    const topic = data.data.topic;
    const page = data.data;
    const difficulties = ["easy", "medium", "hard"].map((code) => `
      <option value="${code}" ${code === "medium" ? "selected" : ""}>${esc(difficultyLabel(code))}</option>
    `).join("");
    modalCard.innerHTML = `
      ${teacherModalHead("📚 " + esc(topic.title), `${page.total} заданий`)}
      <div class="btn-row">
        ${topic.is_active
          ? '<button class="btn btn-good" disabled>✅ Активная тема</button>'
          : `<button class="btn btn-soft" data-action="teacher-topic-activate" data-id="${topic.id}">Сделать активной</button>`}
      </div>
      <div class="section-title">Добавить задание</div>
      <div class="teacher-form">
        <label>Текст</label>
        <textarea class="textarea" id="teacher-task-text" placeholder="Текст задания, можно с формулами $...$"></textarea>
        <label>Ответ (необязательно)</label>
        <input class="input" id="teacher-task-answer" placeholder="Эталонный ответ" autocomplete="off">
        <label>Сложность</label>
        <select class="select" id="teacher-task-difficulty">${difficulties}</select>
        <button class="btn btn-primary" data-action="teacher-add-task" data-id="${topic.id}">Добавить задание</button>
      </div>
      <div class="section-title">Задания темы</div>
      ${page.items.length ? page.items.map((task) => `
        <div class="teacher-task-row">
          <b>#${task.id}</b>
          <span class="teacher-tag">${esc(difficultyLabel(task.difficulty))}</span>
          ${task.target_user_id ? '<span class="teacher-tag warn">индивидуальное</span>' : ""}
          <div class="txt">${esc(task.text)}</div>
          ${task.answer ? `<div class="ans">Ответ: ${esc(task.answer)}</div>` : '<div class="small muted mt8">Без эталона — уйдёт на проверку</div>'}
        </div>
      `).join("") : `<div class="teacher-hint">В этой теме пока нет заданий.</div>`}
      ${teacherPager(page.page, page.pages, "teacher-topic-tasks-page", `data-id="${topic.id}"`)}
    `;
    showModal();
    renderMath(modalCard);
  }

  async function activateTeacherTopic(id, button) {
    button.disabled = true;
    try {
      await api(`/api/teacher/topics/${id}/activate`, { method: "POST" });
      state.teacher.overview = null;
      toast("Тема стала активной", "good");
      haptic("success");
      await loadTeacherSection("topics");
      await openTeacherTopic(id);
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function addTeacherTask(id, button) {
    const text = document.getElementById("teacher-task-text")?.value.trim() || "";
    const answer = document.getElementById("teacher-task-answer")?.value.trim() || "";
    const difficulty = document.getElementById("teacher-task-difficulty")?.value || "medium";
    if (!text) {
      toast("Напиши текст задания", "bad");
      return;
    }
    button.disabled = true;
    try {
      const currentPage = state.teacher.modal?.data?.page || 0;
      await api(`/api/teacher/topics/${id}/tasks`, {
        method: "POST",
        body: JSON.stringify({ text, answer: answer || null, difficulty }),
      });
      state.teacher.overview = null;
      toast("Задание добавлено", "good");
      haptic("success");
      await openTeacherTopic(id, currentPage);
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  function openTeacherNewTopic() {
    state.teacher.modal = { type: "new-topic" };
    modalCard.innerHTML = `
      ${teacherModalHead("➕ Новая тема")}
      <div class="teacher-form">
        <label>Название темы</label>
        <input class="input" id="teacher-new-topic-title" placeholder="Например: Квадратные уравнения" autocomplete="off">
        <label class="checkline"><input type="checkbox" id="teacher-new-topic-active" checked> Сразу сделать активной</label>
        <button class="btn btn-primary" data-action="teacher-create-topic">Создать тему</button>
      </div>
    `;
    showModal();
  }

  async function createTeacherTopic(button) {
    const title = document.getElementById("teacher-new-topic-title")?.value.trim() || "";
    const activate = Boolean(document.getElementById("teacher-new-topic-active")?.checked);
    if (!title) {
      toast("Введи название темы", "bad");
      return;
    }
    button.disabled = true;
    try {
      await api("/api/teacher/topics", {
        method: "POST",
        body: JSON.stringify({ title, activate }),
      });
      state.teacher.overview = null;
      toast("Тема создана", "good");
      haptic("success");
      closeModal();
      state.teacher.topicPage = 0;
      await loadTeacherSection("topics");
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  function openTeacherImport() {
    state.teacher.modal = { type: "import" };
    modalCard.innerHTML = `
      ${teacherModalHead("📥 Импорт заданий", "Только добавление, без удаления")}
      <div class="teacher-hint">
        Формат: строка <b>[topic] Название | medium</b>, затем задания и строка <b>= ответ</b>.
        Разделитель заданий — <b>---</b>.
      </div>
      <textarea class="textarea" id="teacher-import-content" placeholder="[topic] Математика | medium&#10;Найди $2x+3y$&#10;= 16&#10;---&#10;Реши $x^2-5x+6=0$&#10;= 2, 3"></textarea>
      <button class="btn btn-primary btn-block mt12" data-action="teacher-import-submit">Импортировать</button>
    `;
    showModal();
  }

  async function submitTeacherImport(button) {
    const content = document.getElementById("teacher-import-content")?.value || "";
    if (!content.trim()) {
      toast("Вставь текст импорта", "bad");
      return;
    }
    button.disabled = true;
    try {
      const stats = await api("/api/teacher/import", {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      toast(`Тем: ${stats.topics_created}, заданий: ${stats.tasks_added}`, "good");
      state.teacher.overview = null;
      closeModal();
      await loadTeacherSection("overview");
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function seedTeacher(button) {
    button.disabled = true;
    try {
      const stats = await api("/api/teacher/seed", { method: "POST" });
      state.teacher.overview = null;
      toast(`Добавлено тем: ${stats.topics_created}, заданий: ${stats.tasks_added}`, "good");
      await loadTeacherSection(state.teacher.section);
    } catch (error) {
      toast(error.message, "bad");
    } finally {
      button.disabled = false;
    }
  }

  async function assignAll(button) {
    button.disabled = true;
    try {
      const result = await api("/api/teacher/assign-all", { method: "POST" });
      state.teacher.overview = null;
      toast(`Выдано: ${result.sent} из ${result.total}${result.errors ? `, ошибок: ${result.errors}` : ""}`, result.errors ? "bad" : "good");
      await loadTeacherSection("overview");
    } catch (error) {
      toast(error.message, "bad");
    } finally {
      button.disabled = false;
    }
  }

  function changeReviewKind(kind) {
    state.teacher.reviewKind = kind;
    loadTeacherSection("reviews");
  }

  function teacherTopicSearch() {
    state.teacher.topicQuery = document.getElementById("teacher-topic-query")?.value.trim() || "";
    state.teacher.topicPage = 0;
    loadTeacherSection("topics");
  }

  function changeTopicPage(page) {
    state.teacher.topicPage = Math.max(0, page);
    loadTeacherSection("topics");
  }

  async function openTeacherReview(id) {
    try {
      const detail = await api(`/api/teacher/reviews/${id}`);
      state.teacher.modal = { type: "review", detail };
      renderTeacherReviewModal();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  function renderTeacherReviewModal() {
    const data = state.teacher.modal;
    if (!data || data.type !== "review") return;
    const review = data.detail;
    const name = review.username ? "@" + review.username : "id:" + review.student_id;
    const canAccept = review.status !== "correct";
    modalCard.innerHTML = `
      ${teacherModalHead(
        review.status === "review" ? "📨 Ответ на проверку" : "❌ Ошибка",
        `${name} · ${review.assigned_date}`,
      )}
      <div class="card">
        <div class="task-text">${esc(review.task_text)}</div>
        <div class="review-answer">Ответ ученика: <span class="wrong">${esc(review.student_answer || "—")}</span></div>
        <div class="review-answer">Эталон: <b>${esc(review.correct_answer || "—")}</b></div>
        ${review.manually_accepted ? '<div class="status-line correct">✅ Уже засчитано вручную</div>' : ""}
      </div>
      <div class="btn-row">
        ${canAccept
          ? `<button class="btn btn-good" data-action="teacher-accept" data-id="${review.id}">✅ Засчитать</button>`
          : '<button class="btn btn-good" disabled>✅ Засчитано</button>'}
        <button class="btn btn-soft" data-action="teacher-bonus" data-id="${review.id}">🎁 Бонус</button>
      </div>
      <div class="teacher-hint mt12">«Засчитать» начислит XP и монеты как за правильный ответ. «Бонус» — только награда за усилие.</div>
    `;
    showModal();
    renderMath(modalCard);
  }

  async function acceptTeacherReview(id, button) {
    button.disabled = true;
    try {
      const result = await api(`/api/teacher/reviews/${id}/accept`, { method: "POST" });
      toast(`Засчитано: +${result.reward.xp} XP, +${result.reward.coins} 🪙`, "good");
      haptic("success");
      state.teacher.overview = null;
      closeModal();
      await loadTeacherSection("reviews");
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function bonusTeacherReview(id, button) {
    button.disabled = true;
    try {
      const result = await api(`/api/teacher/reviews/${id}/bonus`, { method: "POST" });
      toast(`Бонус: +${result.xp} XP, +${result.coins} 🪙`, "good");
      haptic("success");
      closeModal();
      await loadTeacherSection("reviews");
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  // ─────────────────────────── Действия ───────────────────────────

  async function switchTab(tab) {
    state.tab = tab;
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
    haptic("light");

    // Карта и магазин могли устареть после ответов и покупок.
    try {
      if (tab === "map") {
        state.map = await api("/api/map");
        if (state.tab === "map") render();
      } else if (tab === "shop") {
        state.shop = await api("/api/shop");
        if (state.tab === "shop") render();
      } else if (tab === "teacher") {
        await loadTeacherSection(state.teacher.section);
      }
    } catch (_) {}
  }

  async function requestTasks(button) {
    button.disabled = true;
    try {
      const data = await api("/api/tasks/request", { method: "POST" });
      state.tasks = data.tasks || [];
      const me = await api("/api/me");
      state.me = me;
      toast("Задания получены", "good");
      haptic("success");
      render();
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function submitAnswer(assignmentId, button) {
    const input = view.querySelector(`[data-input="${assignmentId}"]`);
    const answer = (input?.value || "").trim();
    if (!answer) {
      toast("Напиши ответ", "bad");
      input?.focus();
      return;
    }
    button.disabled = true;
    try {
      const result = await api(`/api/tasks/${assignmentId}/answer`, {
        method: "POST",
        body: JSON.stringify({ answer }),
      });
      if (result.status === "correct") {
        haptic("success");
        const reward = result.reward || {};
        let message = `+${reward.xp || 0} XP, +${reward.coins || 0} 🪙`;
        if (reward.quest_reward) message += " · квест выполнен!";
        toast(message, "good");
      } else if (result.status === "wrong") {
        haptic("error");
        toast(`Неверно. Правильный ответ: ${result.correct_answer || "—"}`, "bad");
      } else {
        toast(result.message || "Ответ отправлен на проверку");
      }
      await refreshCore();
      render();
    } catch (error) {
      if (error.status === 409) {
        toast("Задание уже закрыто", "bad");
        await refreshCore();
        render();
        return;
      }
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function useHint(assignmentId, button) {
    button.disabled = true;
    try {
      const result = await api(`/api/tasks/${assignmentId}/hint`, { method: "POST" });
      const task = state.tasks.find((item) => item.id === assignmentId);
      if (task) {
        task.hint = result.hint;
        task.used_hint = true;
      }
      if (state.me?.user) state.me.user.coins = result.coins;
      toast(result.paid_with === "item" ? "Подсказка из предмета" : `Списано ${result.price} 🪙`);
      render();
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function buyItem(code, button) {
    button.disabled = true;
    try {
      const result = await api("/api/shop/buy", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      if (state.shop) {
        state.shop.items = result.items || state.shop.items;
        state.shop.coins = result.coins;
      }
      if (state.me?.user) state.me.user.coins = result.coins;
      toast(`Куплено: ${result.item}`, "good");
      haptic("success");
      render();
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  // ─────────────────────────── Босс ───────────────────────────

  async function openBoss() {
    try {
      state.boss = await api("/api/boss");
      renderBossModal();
    } catch (error) {
      toast(error.message, "bad");
    }
  }

  function showModal() {
    modal.classList.remove("hidden");
  }

  function closeModal() {
    modal.classList.add("hidden");
    state.boss = null;
    state.teacher.modal = null;
  }

  function renderBossModal() {
    const boss = state.boss;
    if (!boss) return;

    if (!boss.active) {
      modalCard.innerHTML = `
        <div class="modal-head">
          <div>
            <div class="modal-title">🏰 Босс темы</div>
            <div class="modal-sub">${esc(boss.topic || "—")}</div>
          </div>
          <button class="icon-btn" data-action="close-modal">✕</button>
        </div>
        <p class="muted">5 удачных ответов ломают босса. 3 ошибки — и бой проигран. Подсказок в бою нет.</p>
        ${boss.can_start
          ? '<button class="btn btn-primary btn-block mt12" data-action="start-boss">⚔️ Начать бой</button>'
          : '<div class="empty mt12"><div class="emoji">🗺</div><h3>Тема не назначена</h3><p>Попроси учителя выбрать тему.</p></div>'}
      `;
      showModal();
      return;
    }

    const fight = boss.fight;
    const task = boss.task;
    if (!task) {
      modalCard.innerHTML = `
        <div class="modal-head">
          <div><div class="modal-title">🏰 Босс темы</div><div class="modal-sub">${esc(boss.topic || "—")}</div></div>
          <button class="icon-btn" data-action="close-modal">✕</button>
        </div>
        <p class="muted">Задачи босса закончились. Попробуй начать бой заново.</p>
        <button class="btn btn-primary btn-block mt12" data-action="start-boss">⚔️ Начать заново</button>
      `;
      showModal();
      return;
    }

    modalCard.innerHTML = `
      <div class="modal-head">
        <div>
          <div class="modal-title">🏰 Босс темы</div>
          <div class="modal-sub">${esc(boss.topic || "—")}</div>
        </div>
        <button class="icon-btn" data-action="close-modal">✕</button>
      </div>
      <div class="boss-stats">
        <div class="boss-stat"><b class="hearts">${"❤️".repeat(Math.max(0, fight.hearts))}${"🖤".repeat(Math.max(0, 3 - fight.hearts))}</b><span>сердца</span></div>
        <div class="boss-stat"><b>${fight.hp} / ${fight.total}</b><span>HP босса</span></div>
        <div class="boss-stat"><b>${fight.current_index + 1} / ${fight.total}</b><span>задание</span></div>
      </div>
      <div class="card task-card">
        <div class="task-head">
          <span class="badge ${esc(task.difficulty)}">${esc(difficultyLabel(task.difficulty))}</span>
          <span class="badge">${esc(task.skill_title)}</span>
        </div>
        <div class="task-text">${esc(task.text)}</div>
        <div class="answer-row">
          <input class="input" id="boss-answer" placeholder="Твой ответ" autocomplete="off">
          <button class="btn btn-primary" data-action="boss-answer" data-id="${fight.id}">Ответить</button>
        </div>
      </div>
    `;
    showModal();
    renderMath(modalCard);
  }

  async function startBoss(button) {
    button.disabled = true;
    try {
      state.boss = await api("/api/boss/start", { method: "POST" });
      haptic("success");
      renderBossModal();
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  async function answerBoss(fightId, button) {
    const input = document.getElementById("boss-answer");
    const answer = (input?.value || "").trim();
    if (!answer) {
      toast("Напиши ответ", "bad");
      input?.focus();
      return;
    }
    button.disabled = true;
    try {
      const result = await api(`/api/boss/${fightId}/answer`, {
        method: "POST",
        body: JSON.stringify({ answer }),
      });
      haptic(result.is_correct ? "success" : "error");

      if (result.status === "active") {
        state.boss = {
          active: true,
          fight: result.fight,
          task: result.next_task,
          topic: state.boss?.topic || "",
        };
        renderBossModal();
        toast(result.is_correct ? "✅ Точное попадание!" : "❌ Мимо! Босс забирает сердце.", result.is_correct ? "good" : "bad");
        return;
      }

      if (result.status === "won") {
        state.boss = { active: false, topic: state.boss?.topic || "" };
        modalCard.innerHTML = `
          <div class="modal-head">
            <div><div class="modal-title">🏆 Босс повержен!</div><div class="modal-sub">${esc(state.boss.topic || "")}</div></div>
            <button class="icon-btn" data-action="close-modal">✕</button>
          </div>
          <div class="card center">
            <div style="font-size:44px">🏆</div>
            <h3>Победа</h3>
            <p class="muted">+${result.reward?.xp || 0} XP, +${result.reward?.coins || 0} 🪙</p>
            ${result.reward?.first_win === false ? '<p class="small muted">Повторная победа: награда меньше.</p>' : ""}
            <button class="btn btn-primary btn-block mt12" data-action="close-modal">Забрать награду</button>
          </div>
        `;
        showModal();
        await refreshCore();
        render();
        return;
      }

      if (result.status === "lost") {
        modalCard.innerHTML = `
          <div class="modal-head">
            <div><div class="modal-title">💀 Босс выстоял</div><div class="modal-sub">${esc(state.boss?.topic || "")}</div></div>
            <button class="icon-btn" data-action="close-modal">✕</button>
          </div>
          <div class="card center">
            <div style="font-size:44px">💀</div>
            <h3>В этот раз не вышло</h3>
            <p class="muted">${esc(result.reason || "Сердца закончились.")}</p>
            <p class="small muted">Потренируйся и возвращайся.</p>
            <button class="btn btn-primary btn-block mt12" data-action="close-modal">Закрыть</button>
          </div>
        `;
        showModal();
        await refreshCore();
        render();
      }
    } catch (error) {
      toast(error.message, "bad");
      button.disabled = false;
    }
  }

  // ─────────────────────────── События ───────────────────────────

  async function onViewClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;

    if (action === "go-tasks") return switchTab("tasks");
    if (action === "go-map") return switchTab("map");
    if (action === "go-shop") return switchTab("shop");
    if (action === "go-hero") return switchTab("hero");
    if (action === "retry") return loadAll();
    if (action === "close-app") { try { tg?.close(); } catch (_) {} return; }
    if (action === "request-tasks") return requestTasks(target);
    if (action === "answer") return submitAnswer(Number(target.dataset.id), target);
    if (action === "hint") return useHint(Number(target.dataset.id), target);
    if (action === "buy") return buyItem(target.dataset.code, target);
    if (action === "open-boss") return openBoss();

    // ── Учительская панель ──
    if (action === "teacher-section") return loadTeacherSection(target.dataset.section);
    if (action === "teacher-refresh") return loadTeacherSection(state.teacher.section);
    if (action === "teacher-assign-all") return assignAll(target);
    if (action === "teacher-import") return openTeacherImport();
    if (action === "teacher-seed") return seedTeacher(target);
    if (action === "teacher-student") return openTeacherStudent(Number(target.dataset.id));
    if (action === "teacher-topic") return openTeacherTopic(Number(target.dataset.id));
    if (action === "teacher-topic-search") return teacherTopicSearch();
    if (action === "teacher-new-topic") return openTeacherNewTopic();
    if (action === "teacher-topic-page") return changeTopicPage(Number(target.dataset.page));
    if (action === "teacher-review-kind") return changeReviewKind(target.dataset.kind);
    if (action === "teacher-review") return openTeacherReview(Number(target.dataset.id));
  }

  async function onModalClick(event) {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;

    if (action === "close-modal") return closeModal();
    if (action === "start-boss") return startBoss(target);
    if (action === "boss-answer") return answerBoss(Number(target.dataset.id), target);

    // ── Учительская панель ──
    if (action === "teacher-save-lesson") return saveTeacherLesson(Number(target.dataset.id), target);
    if (action === "teacher-assign") return assignTeacherTasks(Number(target.dataset.id), target);
    if (action === "teacher-individual") return openTeacherIndividual(Number(target.dataset.id));
    if (action === "teacher-create-individual") return createTeacherIndividual(Number(target.dataset.id), target);
    if (action === "teacher-topic-activate") return activateTeacherTopic(Number(target.dataset.id), target);
    if (action === "teacher-add-task") return addTeacherTask(Number(target.dataset.id), target);
    if (action === "teacher-topic-tasks-page") return openTeacherTopic(Number(target.dataset.id), Number(target.dataset.page));
    if (action === "teacher-create-topic") return createTeacherTopic(target);
    if (action === "teacher-import-submit") return submitTeacherImport(target);
    if (action === "teacher-accept") return acceptTeacherReview(Number(target.dataset.id), target);
    if (action === "teacher-bonus") return bonusTeacherReview(Number(target.dataset.id), target);
  }

  function onViewInput(event) {
    if (event.target.id !== "map-search") return;
    const query = event.target.value.trim().toLowerCase();
    const rows = (state.map?.rows || []).filter((row) => row.title.toLowerCase().includes(query));
    const list = document.getElementById("map-list");
    if (list) list.innerHTML = mapRowsHtml(rows);
  }

  function onViewKeydown(event) {
    if (event.key !== "Enter") return;
    if (event.target.id === "teacher-topic-query") {
      event.preventDefault();
      teacherTopicSearch();
      return;
    }
    if (!event.target.matches(".answer-input")) return;
    const assignmentId = Number(event.target.dataset.input);
    const button = view.querySelector(`[data-action="answer"][data-id="${assignmentId}"]`);
    if (button) submitAnswer(assignmentId, button);
  }

  function onModalKeydown(event) {
    if (event.key !== "Enter") return;
    const id = event.target.id;
    const click = (selector) => {
      const button = modalCard.querySelector(selector);
      if (button && !button.disabled) button.click();
    };
    if (id === "teacher-new-topic-title") {
      event.preventDefault();
      click('[data-action="teacher-create-topic"]');
    } else if (id === "teacher-individual-answer") {
      event.preventDefault();
      click('[data-action="teacher-create-individual"]');
    } else if (id === "teacher-task-answer") {
      event.preventDefault();
      click('[data-action="teacher-add-task"]');
    }
  }

  async function init() {
    if (tg) {
      tg.ready();
      tg.expand();
      applyTheme();
      tg.onEvent?.("themeChanged", applyTheme);
    }
    tabbar.addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (tab) switchTab(tab.dataset.tab);
    });
    view.addEventListener("click", onViewClick);
    view.addEventListener("input", onViewInput);
    view.addEventListener("keydown", onViewKeydown);
    modal.addEventListener("click", onModalClick);
    modal.addEventListener("keydown", onModalKeydown);
    await loadAll();
  }

  init();
})();
