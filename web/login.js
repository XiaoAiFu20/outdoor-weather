// 登录/注册页逻辑 - 已登录直接跳首页

(function () {
    if (localStorage.getItem('__token')) {
        location.href = '/';
        return;
    }

    const tabLogin = document.getElementById('tab-login');
    const tabRegister = document.getElementById('tab-register');
    const formLogin = document.getElementById('form-login');
    const formRegister = document.getElementById('form-register');
    const errorBox = document.getElementById('error-box');
    const infoBox = document.getElementById('info-box');

    tabLogin.onclick = () => switchTab('login');
    tabRegister.onclick = () => switchTab('register');

    function switchTab(which) {
        const isLogin = which === 'login';
        tabLogin.className = 'flex-1 py-2 text-sm font-medium border-b-2 ' +
            (isLogin ? 'border-emerald-600 text-emerald-700' : 'border-transparent text-slate-500');
        tabRegister.className = 'flex-1 py-2 text-sm font-medium border-b-2 ' +
            (!isLogin ? 'border-emerald-600 text-emerald-700' : 'border-transparent text-slate-500');
        formLogin.classList.toggle('hidden', !isLogin);
        formRegister.classList.toggle('hidden', isLogin);
        hideError();
        hideInfo();
    }

    function showError(msg) {
        errorBox.textContent = msg;
        errorBox.classList.remove('hidden');
    }
    function hideError() { errorBox.classList.add('hidden'); }
    function showInfo(msg) {
        infoBox.textContent = msg;
        infoBox.classList.remove('hidden');
    }
    function hideInfo() { infoBox.classList.add('hidden'); }

    // 解析后端 422/400 错误体, 取出可读消息
    function extractError(data) {
        if (Array.isArray(data.detail)) {
            // pydantic 422 格式: [{loc, msg, type}, ...]
            return data.detail.map(d => d.msg).join('; ');
        }
        if (typeof data.detail === 'string') return data.detail;
        return JSON.stringify(data);
    }

    formLogin.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        if (!username || !password) { showError('请填写完整'); return; }

        const btn = document.getElementById('btn-login-submit');
        btn.disabled = true; btn.textContent = '登录中...';
        try {
            const r = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username, password}),
            });
            const data = await r.json();
            if (!r.ok) {
                showError(extractError(data) || '登录失败');
                return;
            }
            localStorage.setItem('__token', data.access_token);
            localStorage.setItem('__username', data.username);
            location.href = '/';
        } catch (err) {
            showError('网络错误: ' + err.message);
        } finally {
            btn.disabled = false; btn.textContent = '登录';
        }
    });

    formRegister.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideError();
        hideInfo();
        const username = document.getElementById('reg-username').value.trim();
        const email = document.getElementById('reg-email').value.trim();
        const password = document.getElementById('reg-password').value;
        if (!username || !email || !password) { showError('请填写完整'); return; }

        const btn = document.getElementById('btn-register-submit');
        btn.disabled = true; btn.textContent = '注册中...';
        try {
            const r1 = await fetch('/api/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username, email, password}),
            });
            const d1 = await r1.json();
            if (!r1.ok) {
                showError(extractError(d1));
                return;
            }
            // 注册成功, 自动登录
            const r2 = await fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username, password}),
            });
            const d2 = await r2.json();
            if (!r2.ok) {
                showInfo('注册成功, 但自动登录失败, 请手动登录');
                switchTab('login');
                document.getElementById('login-username').value = username;
                return;
            }
            localStorage.setItem('__token', d2.access_token);
            localStorage.setItem('__username', d2.username);
            location.href = '/';
        } catch (err) {
            showError('网络错误: ' + err.message);
        } finally {
            btn.disabled = false; btn.textContent = '注册并登录';
        }
    });
})();
