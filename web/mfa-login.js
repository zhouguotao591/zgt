'use strict';

window.pc28Mfa = (() => {
  const template = `
    <h2 data-mfa-title>谷歌验证器</h2>
    <div data-mfa-setup hidden>
      <p>用 Google Authenticator 扫描二维码，或手工输入密钥。</p>
      <img data-mfa-image width="180" height="180" alt="验证器绑定二维码">
      <label>手工密钥<input data-mfa-key readonly></label>
    </div>
    <form data-mfa-form>
      <label><span data-mfa-label>六位动态码</span><input data-mfa-code required autocomplete="one-time-code"></label>
      <p data-mfa-error role="alert"></p>
      <button type="submit">确认</button>
      <button type="button" class="secondary" data-mfa-recover hidden>使用恢复码</button>
      <button type="button" class="secondary" data-mfa-cancel>返回登录</button>
    </form>
    <div data-mfa-saved hidden>
      <p>请保存以下一次性恢复码。丢失验证器时，可用其中一枚重新绑定。</p>
      <pre data-mfa-codes></pre>
      <button type="button" data-mfa-continue>已保存，继续</button>
    </div>`;

  async function show(firstForm, result, request, setToken, done) {
    const panel = document.createElement('section');
    panel.innerHTML = template;
    firstForm.reset();
    firstForm.hidden = true;
    firstForm.after(panel);
    const find = name => panel.querySelector('[data-mfa-' + name + ']');
    const setup = find('setup');
    const form = find('form');
    const input = find('code');
    const recover = find('recover');
    const error = find('error');
    let stage;
    let recovery = false;
    let busy = false;
    let complete = false;
    let closed = false;
    const call = (action, body = {}) => request('/api/auth/mfa/' + action, body);

    function inputMode(useRecovery) {
      recovery = useRecovery;
      input.value = '';
      input.pattern = recovery ? '[0-9a-fA-F]{24}' : '[0-9]{6}';
      input.maxLength = recovery ? 24 : 6;
      input.inputMode = recovery ? 'text' : 'numeric';
      find('label').textContent = recovery ? '一次性恢复码' : '六位动态码';
      recover.textContent = recovery ? '使用动态码' : '使用恢复码';
      input.focus();
    }

    async function stageView(data) {
      setToken(data.token);
      stage = data.status;
      const verify = stage === 'mfa_verify_required';
      recover.hidden = !verify;
      setup.hidden = true;
      find('title').textContent = verify ? '验证登录' : '绑定谷歌验证器';
      inputMode(false);
      if (!verify) {
        const binding = await call('start');
        find('image').src = binding.image;
        find('key').value = binding.key;
        setup.hidden = false;
        input.focus();
      }
    }

    async function cancel() {
      if (closed) return;
      closed = true;
      if (!complete) {
        try { await call('cancel'); } catch (_) {}
      }
      location.reload();
    }

    find('cancel').onclick = cancel;
    find('continue').onclick = done;
    recover.onclick = () => inputMode(!recovery);
    firstForm.closest('dialog')?.addEventListener('close', cancel, {once:true});
    form.onsubmit = async event => {
      event.preventDefault();
      if (busy || closed) return;
      busy = true;
      const submit = form.querySelector('button[type="submit"]');
      submit.disabled = true;
      error.textContent = '';
      try {
        const action = recovery ? 'recover' : stage === 'mfa_verify_required' ? 'verify' : 'confirm';
        const data = await call(action, {code:input.value.toLowerCase()});
        if (data.status) {
          await stageView(data);
        } else {
          complete = true;
          setToken(data.token);
          if (!data.recovery_codes) {
            done();
            return;
          }
          setup.hidden = true;
          find('key').value = '';
          find('image').removeAttribute('src');
          form.hidden = true;
          find('title').textContent = '绑定成功';
          find('codes').textContent = data.recovery_codes.join('\n');
          find('saved').hidden = false;
          find('continue').focus();
        }
      } catch (e) {
        error.textContent = e.message;
        input.value = '';
        input.focus();
        if (['mfa_expired','invalid_session'].includes(e.code)) submit.hidden = true;
      } finally {
        busy = false;
        submit.disabled = false;
      }
    };
    try {
      await stageView(result);
    } catch (e) {
      error.textContent = e.message;
      form.querySelector('button[type="submit"]').hidden = true;
    }
  }

  return {show};
})();

(() => {
  const button = document.getElementById('account-mfa');
  if (!button) return;
  const scope = document.body.dataset.admin === '1' ? '/admin' : '/member';
  const dialog = document.createElement('dialog');
  dialog.className = 'strategy-dialog';
  dialog.innerHTML = `
    <div class="strategy-dialog-head"><h2>两步验证</h2><button type="button" class="secondary" aria-label="关闭">×</button></div>
    <p data-security-status role="status"></p>
    <form hidden>
      <div class="formgrid">
        <label>当前密码<input name="password" type="password" autocomplete="current-password" maxlength="128" required></label>
        <label>六位动态码<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required></label>
      </div>
      <p data-security-error class="negative" role="alert"></p>
      <button type="submit" value="rotate">更换验证器</button>
      <button type="submit" value="regenerate" class="secondary">重新生成恢复码</button>
    </form>
    <pre data-security-codes hidden></pre>`;
  document.body.append(dialog);
  const form = dialog.querySelector('form');
  const status = dialog.querySelector('[data-security-status]');
  const error = dialog.querySelector('[data-security-error]');
  const codes = dialog.querySelector('[data-security-codes]');
  let csrf = '';
  let busy = false;

  async function request(path, body) {
    const response = await fetch(scope + path, body === undefined ? {} : {
      method: 'POST',
      headers: {'Content-Type':'application/json', 'X-PC28-Token':csrf},
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw Object.assign(Error(result.error || '请求失败'), {code:result.code});
    return result;
  }

  button.onclick = async () => {
    form.hidden = true;
    status.textContent = '正在读取…';
    dialog.showModal();
    try {
      csrf = (await request('/api/session')).token;
      const data = await request('/api/auth/mfa/status');
      status.textContent = data.required
        ? (data.bound ? '已启用登录验证，验证器已绑定。' : '下次登录时请绑定验证器。')
        : (data.bound ? '登录验证已关闭，原绑定仍保留。' : '登录验证未开启。');
      form.hidden = !data.required || !data.bound;
    } catch (e) { status.textContent = e.message; }
  };
  dialog.querySelector('button').onclick = () => { if (!busy) dialog.close(); };
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => {
    form.reset();
    error.textContent = '';
    codes.textContent = '';
    codes.hidden = true;
  });
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const action = event.submitter.value;
    if (action === 'regenerate' && !confirm('重新生成后，旧恢复码将失效。确定继续？')) return;
    busy = true;
    error.textContent = '';
    try {
      const data = await request('/api/auth/mfa/' + action, {
        password:form.elements.password.value, code:form.elements.code.value
      });
      if (data.status) {
        status.textContent = '确认新验证器之前，原绑定继续有效。';
        await window.pc28Mfa.show(form, data, request, value => { csrf = value; }, () => location.reload());
      } else {
        codes.textContent = '请保存新的一次性恢复码\n\n' + data.recovery_codes.join('\n');
        codes.hidden = false;
      }
    } catch (e) { error.textContent = e.message; }
    finally { form.reset(); busy = false; }
  };
})();
