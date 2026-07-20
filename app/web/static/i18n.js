/* Shared client-side i18n for the website (signup, dashboard, automations,
 * settings) - one dictionary instead of duplicating uz/ru/en text across
 * four otherwise-independent static HTML pages (app/web/router.py serves
 * each one with no templating engine, so this file is the one thing they
 * all share).
 *
 * Not to be confused with app/automations/router.py's uz/ru template
 * localization (customer-facing DM/reply copy) - this only controls the
 * MERCHANT'S OWN dashboard chrome. Persisted server-side on
 * Merchant.ui_language (PATCH /auth/me) once logged in, and in
 * localStorage before that resolves / on the pre-auth signup page.
 */
(function (global) {
  var STORAGE_KEY = 'uiLanguage';
  var SUPPORTED = ['uz', 'ru', 'en'];

  var DICT = {
    uz: {
      'nav.home': "Bosh sahifa",
      'nav.inbox': "Suhbatlar",
      'nav.soon': "tez kunda",
      'nav.automation': "Avtomatlashtirish",
      'nav.settings': "Sozlamalar",
      'sidebar.logout': "Chiqish",
      'common.loading': "Yuklanmoqda...",
      'common.account': "Akkount",
      'common.connect': "Ulash",
      'common.connected': "Ulangan",
      'common.notConnected': "Ulanmagan",
      'common.error': "Xatolik yuz berdi",

      'home.promoTitle': "Telegram × Dukan AI. Endi birga ishlaymiz",
      'home.promoSub': "Instagram bilan bir qatorda Telegram orqali ham mijozlaringiz bilan avtomatik suhbatlashing.",
      'home.promoBadge': "Faol",
      'home.greetingHi': "Salom, {name}!",
      'home.greetingDefault': "Xush kelibsiz!",
      'home.statusLine': "{connected} tadan {total} ta kanal ulangan",
      'home.startHere': "Boshlash uchun",
      'home.allTemplates': "Barcha shablonlar",
      'home.quickTag': "Tezkor avtomatlashtirish",
      'home.popular': "MASHHUR",
      'home.nextSteps': "Keyingi eng yaxshi qadamlar",
      'home.nextStepsSub': "Botingizni ishga tushirish uchun quyidagilarni bajaring.",
      'home.stepTelegram': "Telegram botni ulang",
      'home.stepInstagram': "Instagram akkountingizni ulang",
      'home.stepAutomation': "Birinchi avtomatlashtirishni o'rnating",
      'home.install': "O'rnatish",

      'settings.title': "Sozlamalar",
      'settings.sub': "Akkountingiz va ulanishlaringizni shu yerdan boshqaring.",
      'settings.account': "Akkount",
      'settings.name': "Ism",
      'settings.email': "Email",
      'settings.phone': "Telefon",
      'settings.connections': "Ulanishlar",
      'settings.telegramBot': "Telegram bot",
      'settings.instagram': "Instagram",
      'settings.language': "Til",

      'auto.title': "Avtomatlashtirish",
      'auto.sub': "Instagram izohlariga avtomatik javob bering.",
      'auto.connectFirst': "Boshlash uchun avval Instagram akkountingizni ulang.",
      'auto.connectIg': "Instagram-ni ulash",
      'auto.installed': "O'rnatilgan",
      'auto.templates': "Shablonlar",
      'auto.install': "O'rnatish",
      'auto.edit': "Tahrirlash",
      'auto.delete': "O'chirish",
      'auto.confirmDelete': "O'chirishni tasdiqlaysizmi?",
      'auto.reconnectRequired': "Instagram ulanishi eskirgan. Sozlamalardan qayta ulang.",
      'auto.cancel': "Bekor qilish",
      'auto.next': "Keyingi",
      'auto.back': "Orqaga",
      'auto.activate': "Faollashtirish",
      'auto.activateAnyway': "Baribir faollashtirish",
      'auto.publicReplyLabel': "Izoh ostida ochiq javob",
      'auto.allPosts': "Barcha postlar",
      'auto.specificPosts': "Tanlangan postlar",
      'auto.step2TitleComment': "Postlarni tanlang",
      'auto.step2SubComment': "Qaysi postlar ostidagi izohlarga ishlasin?",
      'auto.step2TitleStory': "Tayyor",
      'auto.step2SubStory': "Bu avtomatlashtirish barcha hikoyalaringizga javob yozganlarga ishlaydi.",
      'auto.fillAllFields': "Barcha maydonlarni to'ldiring.",
      'auto.channelComment': "Izoh",
      'auto.channelStory': "Hikoya javobi",
      'auto.scopeAllPosts': "hamma postlar",
      'auto.scopeAllStories': "barcha hikoyalar",
      'auto.weekly': "Shu hafta",
      'auto.statComment': "ta izoh",
      'auto.statStory': "ta javob",
      'auto.statDm': "ta DM",
      'auto.collisionWarning': "Bu kalit so'z \"{name}\" qoidasida ham ishlatilmoqda — ikkalasi ham ishlaydi, lekin ustunlik postga bog'langan qoidaga beriladi. Davom etish uchun yana bosing.",

      'signup.title': "Ro'yxatdan o'tish",
      'signup.subtitle': "3 daqiqada, kartasiz. Keyin Telegram va Instagram akkauntlaringizni ulaysiz.",
      'signup.name': "Ismingiz",
      'signup.email': "Email",
      'signup.phone': "Telefon raqami",
      'signup.password': "Parol",
      'signup.passwordConfirm': "Parolni tasdiqlang",
      'signup.passwordPlaceholder': "Kamida 8 ta belgi",
      'signup.passwordConfirmPlaceholder': "Parolni qayta kiriting",
      'signup.submit': "Ro'yxatdan o'tish",
      'signup.haveAccount': "Allaqachon akkountingiz bormi?",
      'signup.login': "Kirish",
      'signup.passwordMismatch': "Parollar mos kelmadi.",
      'login.title': "Kirish",
      'login.subtitle': "Email va parolingiz bilan kiring.",
      'login.noAccount': "Akkountingiz yo'qmi?",
      'login.signup': "Ro'yxatdan o'tish",
      'code.title': "Kodni kiriting",
      'code.subtitle': "{email} manziliga 6 xonali kod yubordik.",
      'code.submit': "Tasdiqlash",
      'code.resendHint': "Kod kelmadimi?",
      'code.resend': "Qayta yuborish",
      'code.resent': "Yangi kod yuborildi.",
      'success.title': "Xush kelibsiz!",
      'success.subtitle': "Akkountingiz tayyor. Endi ijtimoiy tarmoqlaringizni ulang.",
      'success.connectInstagram': "Instagram bilan ulash",
      'success.connectTelegram': "Telegram botni ulash",
      'success.openDashboard': "Boshqaruv panelini ochish",
    },
    ru: {
      'nav.home': "Главная",
      'nav.inbox': "Чаты",
      'nav.soon': "скоро",
      'nav.automation': "Автоматизация",
      'nav.settings': "Настройки",
      'sidebar.logout': "Выйти",
      'common.loading': "Загрузка...",
      'common.account': "Аккаунт",
      'common.connect': "Подключить",
      'common.connected': "Подключено",
      'common.notConnected': "Не подключено",
      'common.error': "Произошла ошибка",

      'home.promoTitle': "Telegram × Dukan AI. Теперь работаем вместе",
      'home.promoSub': "Общайтесь с клиентами автоматически не только в Instagram, но и в Telegram.",
      'home.promoBadge': "Активно",
      'home.greetingHi': "Привет, {name}!",
      'home.greetingDefault': "Добро пожаловать!",
      'home.statusLine': "Подключено {connected} из {total} каналов",
      'home.startHere': "Начните здесь",
      'home.allTemplates': "Все шаблоны",
      'home.quickTag': "Быстрая автоматизация",
      'home.popular': "ПОПУЛЯРНОЕ",
      'home.nextSteps': "Ваши следующие шаги",
      'home.nextStepsSub': "Выполните следующее, чтобы запустить бота.",
      'home.stepTelegram': "Подключите Telegram-бота",
      'home.stepInstagram': "Подключите аккаунт Instagram",
      'home.stepAutomation': "Установите первую автоматизацию",
      'home.install': "Установить",

      'settings.title': "Настройки",
      'settings.sub': "Управляйте аккаунтом и подключениями здесь.",
      'settings.account': "Аккаунт",
      'settings.name': "Имя",
      'settings.email': "Email",
      'settings.phone': "Телефон",
      'settings.connections': "Подключения",
      'settings.telegramBot': "Telegram-бот",
      'settings.instagram': "Instagram",
      'settings.language': "Язык",

      'auto.title': "Автоматизация",
      'auto.sub': "Автоматически отвечайте на комментарии в Instagram.",
      'auto.connectFirst': "Сначала подключите аккаунт Instagram, чтобы начать.",
      'auto.connectIg': "Подключить Instagram",
      'auto.installed': "Установленные",
      'auto.templates': "Шаблоны",
      'auto.install': "Установить",
      'auto.edit': "Изменить",
      'auto.delete': "Удалить",
      'auto.confirmDelete': "Удалить это?",
      'auto.reconnectRequired': "Подключение к Instagram устарело. Переподключите в настройках.",
      'auto.cancel': "Отмена",
      'auto.next': "Далее",
      'auto.back': "Назад",
      'auto.activate': "Активировать",
      'auto.activateAnyway': "Всё равно активировать",
      'auto.publicReplyLabel': "Публичный ответ под комментарием",
      'auto.allPosts': "Все посты",
      'auto.specificPosts': "Выбранные посты",
      'auto.step2TitleComment': "Выберите посты",
      'auto.step2SubComment': "Под какими постами реагировать на комментарии?",
      'auto.step2TitleStory': "Готово",
      'auto.step2SubStory': "Эта автоматизация сработает на ответы ко всем вашим Историям.",
      'auto.fillAllFields': "Заполните все поля.",
      'auto.channelComment': "Комментарий",
      'auto.channelStory': "Ответ в Историях",
      'auto.scopeAllPosts': "все посты",
      'auto.scopeAllStories': "все истории",
      'auto.weekly': "За неделю",
      'auto.statComment': "комментариев",
      'auto.statStory': "ответов",
      'auto.statDm': "DM",
      'auto.collisionWarning': "Это ключевое слово уже используется в правиле \"{name}\" — сработают оба, но приоритет у правила, привязанного к посту. Нажмите ещё раз, чтобы продолжить.",

      'signup.title': "Регистрация",
      'signup.subtitle': "3 минуты, без карты. Затем подключите Telegram и Instagram.",
      'signup.name': "Ваше имя",
      'signup.email': "Email",
      'signup.phone': "Номер телефона",
      'signup.password': "Пароль",
      'signup.passwordConfirm': "Подтвердите пароль",
      'signup.passwordPlaceholder': "Минимум 8 символов",
      'signup.passwordConfirmPlaceholder': "Введите пароль ещё раз",
      'signup.submit': "Зарегистрироваться",
      'signup.haveAccount': "Уже есть аккаунт?",
      'signup.login': "Войти",
      'signup.passwordMismatch': "Пароли не совпадают.",
      'login.title': "Вход",
      'login.subtitle': "Войдите с помощью email и пароля.",
      'login.noAccount': "Нет аккаунта?",
      'login.signup': "Регистрация",
      'code.title': "Введите код",
      'code.subtitle': "Мы отправили 6-значный код на {email}.",
      'code.submit': "Подтвердить",
      'code.resendHint': "Код не пришёл?",
      'code.resend': "Отправить снова",
      'code.resent': "Новый код отправлен.",
      'success.title': "Добро пожаловать!",
      'success.subtitle': "Ваш аккаунт готов. Теперь подключите соцсети.",
      'success.connectInstagram': "Подключить Instagram",
      'success.connectTelegram': "Подключить Telegram-бота",
      'success.openDashboard': "Открыть панель управления",
    },
    en: {
      'nav.home': "Home",
      'nav.inbox': "Inbox",
      'nav.soon': "soon",
      'nav.automation': "Automation",
      'nav.settings': "Settings",
      'sidebar.logout': "Log out",
      'common.loading': "Loading...",
      'common.account': "Account",
      'common.connect': "Connect",
      'common.connected': "Connected",
      'common.notConnected': "Not connected",
      'common.error': "Something went wrong",

      'home.promoTitle': "Telegram × Dukan AI. Now we're working together",
      'home.promoSub': "Automatically chat with customers over Telegram, alongside Instagram.",
      'home.promoBadge': "Active",
      'home.greetingHi': "Hi, {name}!",
      'home.greetingDefault': "Welcome!",
      'home.statusLine': "{connected} of {total} channels connected",
      'home.startHere': "Start here",
      'home.allTemplates': "All templates",
      'home.quickTag': "Quick automation",
      'home.popular': "POPULAR",
      'home.nextSteps': "Your next best moves",
      'home.nextStepsSub': "Do the following to get your bot up and running.",
      'home.stepTelegram': "Connect your Telegram bot",
      'home.stepInstagram': "Connect your Instagram account",
      'home.stepAutomation': "Install your first automation",
      'home.install': "Install",

      'settings.title': "Settings",
      'settings.sub': "Manage your account and connections here.",
      'settings.account': "Account",
      'settings.name': "Name",
      'settings.email': "Email",
      'settings.phone': "Phone",
      'settings.connections': "Connections",
      'settings.telegramBot': "Telegram bot",
      'settings.instagram': "Instagram",
      'settings.language': "Language",

      'auto.title': "Automation",
      'auto.sub': "Automatically reply to Instagram comments.",
      'auto.connectFirst': "Connect your Instagram account first to get started.",
      'auto.connectIg': "Connect Instagram",
      'auto.installed': "Installed",
      'auto.templates': "Templates",
      'auto.install': "Install",
      'auto.edit': "Edit",
      'auto.delete': "Delete",
      'auto.confirmDelete': "Delete this?",
      'auto.reconnectRequired': "Your Instagram connection expired. Reconnect it from Settings.",
      'auto.cancel': "Cancel",
      'auto.next': "Next",
      'auto.back': "Back",
      'auto.activate': "Activate",
      'auto.activateAnyway': "Activate anyway",
      'auto.publicReplyLabel': "Public reply under comment",
      'auto.allPosts': "All posts",
      'auto.specificPosts': "Selected posts",
      'auto.step2TitleComment': "Choose posts",
      'auto.step2SubComment': "Which posts' comments should this apply to?",
      'auto.step2TitleStory': "Ready",
      'auto.step2SubStory': "This automation will trigger on replies to any of your Stories.",
      'auto.fillAllFields': "Fill in all fields.",
      'auto.channelComment': "Comment",
      'auto.channelStory': "Story reply",
      'auto.scopeAllPosts': "all posts",
      'auto.scopeAllStories': "all stories",
      'auto.weekly': "This week",
      'auto.statComment': "comments",
      'auto.statStory': "replies",
      'auto.statDm': "DMs",
      'auto.collisionWarning': "This keyword is also used by \"{name}\" — both will fire, but the post-scoped rule takes priority. Click again to continue anyway.",

      'signup.title': "Sign up",
      'signup.subtitle': "3 minutes, no card. You'll connect Telegram and Instagram next.",
      'signup.name': "Your name",
      'signup.email': "Email",
      'signup.phone': "Phone number",
      'signup.password': "Password",
      'signup.passwordConfirm': "Confirm password",
      'signup.passwordPlaceholder': "At least 8 characters",
      'signup.passwordConfirmPlaceholder': "Re-enter your password",
      'signup.submit': "Sign up",
      'signup.haveAccount': "Already have an account?",
      'signup.login': "Log in",
      'signup.passwordMismatch': "Passwords don't match.",
      'login.title': "Log in",
      'login.subtitle': "Log in with your email and password.",
      'login.noAccount': "Don't have an account?",
      'login.signup': "Sign up",
      'code.title': "Enter the code",
      'code.subtitle': "We sent a 6-digit code to {email}.",
      'code.submit': "Confirm",
      'code.resendHint': "Didn't get the code?",
      'code.resend': "Resend",
      'code.resent': "A new code was sent.",
      'success.title': "Welcome!",
      'success.subtitle': "Your account is ready. Now connect your social accounts.",
      'success.connectInstagram': "Connect Instagram",
      'success.connectTelegram': "Connect Telegram bot",
      'success.openDashboard': "Open dashboard",
    },
  };

  var LABELS = { uz: 'UZ', ru: 'RU', en: 'EN' };

  function getLang() {
    var saved = global.localStorage.getItem(STORAGE_KEY);
    return SUPPORTED.indexOf(saved) !== -1 ? saved : 'uz';
  }

  function t(key, vars) {
    var lang = getLang();
    var text = (DICT[lang] && DICT[lang][key]) || (DICT.uz && DICT.uz[key]) || key;
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        text = text.replace('{' + k + '}', vars[k]);
      });
    }
    return text;
  }

  function applyTranslations(root) {
    document.documentElement.setAttribute('lang', getLang());
    var scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
    });
  }

  var _styleInjected = false;
  function _injectStyle() {
    if (_styleInjected) return;
    _styleInjected = true;
    var style = document.createElement('style');
    style.textContent =
      '.i18n-switch{display:flex;gap:4px;padding:3px;border-radius:10px;' +
      'background:var(--surface-alt,rgba(127,127,127,.12));margin-bottom:10px;}' +
      '.i18n-switch button{flex:1;border:none;background:transparent;cursor:pointer;' +
      'font-family:inherit;font-size:11.5px;font-weight:700;padding:6px 0;border-radius:8px;' +
      'color:var(--text-4,#888);}' +
      '.i18n-switch button.active{background:var(--surface,#fff);color:var(--primary-text,inherit);}';
    document.head.appendChild(style);
  }

  function renderSwitcher(containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    _injectStyle();
    container.classList.add('i18n-switch');
    container.innerHTML = '';
    SUPPORTED.forEach(function (lang) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = LABELS[lang];
      btn.className = lang === getLang() ? 'active' : '';
      btn.addEventListener('click', function () {
        setLang(lang);
      });
      container.appendChild(btn);
    });
  }

  var _switcherIds = [];
  var _onChange = null;

  function setLang(lang) {
    if (SUPPORTED.indexOf(lang) === -1) return;
    global.localStorage.setItem(STORAGE_KEY, lang);
    applyTranslations();
    _switcherIds.forEach(renderSwitcher);
    if (_onChange) _onChange(lang);
  }

  function initI18n(switcherId) {
    applyTranslations();
    if (switcherId) {
      _switcherIds.push(switcherId);
      renderSwitcher(switcherId);
    }
  }

  // Called once /auth/me resolves: the server's stored preference wins
  // over whatever localStorage had (e.g. a fresh browser, or a change
  // made on another device) - but only if it actually differs, so we
  // don't clobber a same-tab change made a moment earlier.
  function syncLangFromMerchant(uiLanguage, onLocalChange) {
    _onChange = onLocalChange || null;
    if (uiLanguage && SUPPORTED.indexOf(uiLanguage) !== -1 && uiLanguage !== getLang()) {
      global.localStorage.setItem(STORAGE_KEY, uiLanguage);
      applyTranslations();
      _switcherIds.forEach(renderSwitcher);
    }
  }

  global.Dukan18n = {
    t: t,
    getLang: getLang,
    setLang: setLang,
    applyTranslations: applyTranslations,
    initI18n: initI18n,
    syncLangFromMerchant: syncLangFromMerchant,
  };
})(window);
