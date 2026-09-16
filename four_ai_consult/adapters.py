from __future__ import annotations

import json
from dataclasses import dataclass


def normalize_input_text(text: str) -> str:
    """Normalize browser line endings, not meaningful spaces or indentation."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").replace("\u200b", "").strip()


@dataclass(frozen=True)
class SiteAdapter:
    id: str
    name: str
    home_url: str
    input_selectors: tuple[str, ...]
    send_selectors: tuple[str, ...]
    assistant_selectors: tuple[str, ...]
    stop_selectors: tuple[str, ...]
    native_input: bool = False
    require_completion_evidence: bool = False
    completion_markers: tuple[str, ...] = ()
    completion_quiet_seconds: float = 0.0
    completion_fallback_quiet_seconds: float = 0.0
    completion_fallback_min_chars: int = 0
    completion_fallback_action_count: int = 0
    # Some sites render a complete answer in one atomic DOM update and expose
    # neither a labelled Stop button nor a labelled Regenerate button.  This
    # is the final, site-independent safety net: accept only a new, non-busy,
    # non-reasoning answer after this uninterrupted quiet window.  Any text
    # mutation resets the window in WebPane.
    completion_absolute_quiet_seconds: float = 0.0
    completion_absolute_min_chars: int = 1
    login_selectors: tuple[str, ...] = ()

    def snapshot_script(self) -> str:
        config = json.dumps(
            {
                "input": self.input_selectors,
                "assistant": self.assistant_selectors,
                "stop": self.stop_selectors,
                "completionMarkers": self.completion_markers,
                "login": self.login_selectors,
            },
            ensure_ascii=False,
        )
        return _SNAPSHOT_SCRIPT.replace("__CONFIG__", config)

    def send_script(self, text: str) -> str:
        config = json.dumps(
            {
                "input": self.input_selectors,
                "send": self.send_selectors,
                "text": text,
            },
            ensure_ascii=False,
        )
        return _SEND_SCRIPT.replace("__CONFIG__", config)

    def focus_input_script(self) -> str:
        config = json.dumps({"input": self.input_selectors}, ensure_ascii=False)
        return _FOCUS_INPUT_SCRIPT.replace("__CONFIG__", config)

    def diagnostic_script(self) -> str:
        config = json.dumps(
            {
                "input": self.input_selectors,
                "send": self.send_selectors,
                "assistant": self.assistant_selectors,
                "stop": self.stop_selectors,
                "login": self.login_selectors,
            },
            ensure_ascii=False,
        )
        return _DIAGNOSTIC_SCRIPT.replace("__CONFIG__", config)

    def readiness_script(self) -> str:
        # No messages, editor contents, URLs or account information are returned.
        return """(() => {
          const cfg = __CONFIG__;
          const usable = (el) => {
            const style = getComputedStyle(el);
            return el.getClientRects().length > 0 && style.visibility !== 'hidden' &&
              style.display !== 'none' && !el.disabled && !el.readOnly &&
              el.getAttribute('aria-disabled') !== 'true';
          };
          const any = (selectors) => selectors.some(selector => {
            try { return Array.from(document.querySelectorAll(selector)).some(usable); }
            catch (_) { return false; }
          });
          return JSON.stringify({
            ok: true,
            inputAvailable: any(cfg.input),
            loginRequired: any(cfg.login)
          });
        })()""".replace("__CONFIG__", json.dumps({
            "input": self.input_selectors,
            "login": self.login_selectors,
        }))


_SNAPSHOT_SCRIPT = r"""
(function () {
  const cfg = __CONFIG__;
  const visible = (el) => {
    if (!el) return false;
    for (let node = el; node; node = node.parentElement) {
      const style = window.getComputedStyle(node);
      if (node.hidden || style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  };
  const disabled = (el) => !!el && (!!el.disabled || el.getAttribute('aria-disabled') === 'true' ||
    el.getAttribute('data-disabled') === 'true' || /(?:^|\s)(?:is-)?disabled(?:\s|$)/i.test(
      typeof el.className === 'string' ? el.className : ''));
  const all = (selectors) => {
    const found = [];
    const seen = new Set();
    for (const selector of selectors) {
      try {
        for (const el of document.querySelectorAll(selector)) {
          if (!seen.has(el)) { seen.add(el); found.push(el); }
        }
      } catch (_) {}
    }
    // Selectors are ordered by specificity, not by message chronology. Restore
    // document order so the final non-empty element is the latest answer.
    return found.sort((left, right) => {
      if (left === right) return 0;
      return left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
    });
  };
  const clean = (value) => (value || '').replace(/\u200b/g, '').replace(/[ \t]+\n/g, '\n').trim();
  const reasoningSelector = [
    '[class*="thinking"]', '[class*="reasoning"]', '[class*="thought"]',
    '[data-role="reasoning"]', '[data-testid*="thinking"]',
    '[data-testid*="reasoning"]', '[data-testid*="thought"]',
    '[class*="search-process"]', '[data-testid*="search-process"]',
    '[class*="toolcall-content"]', '[class*="tool-call-content"]',
    '[aria-label*="思考过程"]'
  ].join(',');
  const excludedAnswerSelector = [
    'button', '[role="button"]', '[class*="suggest-message"]',
    '[class*="suggestion"]', '[data-testid*="suggest"]', '[class*="follow-up"]',
    '[class*="message-action"]', '[class*="action-bar"]',
    '[class*="message-footer"]', '[class*="message-toolbar"]', reasoningSelector
  ].join(',');
  const answerText = (element) => {
    // Walk the live tree so CSS-hidden translations, search traces and
    // reasoning panels cannot leak into an otherwise Chinese final answer.
    // Preserve the visible model response's headings, lists, code and tables.
    const markdown = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      const tag = node.tagName.toLowerCase();
      if (['script', 'style', 'noscript', 'svg'].includes(tag)) return '';
      if (!visible(node) || node.matches(excludedAnswerSelector)) return '';
      if (tag === 'br') return '\n';
      if (tag === 'pre') {
        const code = node.textContent || '';
        const fence = '`'.repeat(Math.max(3, ...Array.from(code.matchAll(/`+/g), m => m[0].length + 1)));
        return '\n\n' + fence + '\n' + code + '\n' + fence + '\n\n';
      }
      if (tag === 'table') {
        const rows = Array.from(node.querySelectorAll('tr')).filter(visible).map(row =>
          Array.from(row.children).filter(visible).map(cell => (cell.textContent || '').trim().replace(/\|/g, '\\|').replace(/\n/g, ' '))
        ).filter(row => row.length);
        if (!rows.length) return '';
        const width = Math.max(...rows.map(row => row.length));
        const line = row => '| ' + Array.from({length:width}, (_, i) => row[i] || '').join(' | ') + ' |';
        return '\n\n' + [line(rows[0]), line(Array(width).fill('---')), ...rows.slice(1).map(line)].join('\n') + '\n\n';
      }
      const content = Array.from(node.childNodes).map(markdown).join('');
      if (/^h[1-6]$/.test(tag)) return '\n\n' + '#'.repeat(Number(tag[1])) + ' ' + content.trim() + '\n\n';
      if (tag === 'li') {
        const parent = node.parentElement;
        const ordered = parent?.tagName.toLowerCase() === 'ol';
        const start = Number(parent?.getAttribute('start') || 1);
        const number = Number(node.getAttribute('value') || (start + Array.from(parent.children).indexOf(node)));
        return '\n' + (ordered ? number + '. ' : '- ') + content.trim() + '\n';
      }
      if (tag === 'strong' || tag === 'b') return '**' + content + '**';
      if (tag === 'code') return '`' + content + '`';
      if (tag === 'a') {
        const href = node.getAttribute('href') || '';
        return /^https?:\/\//i.test(href) ? '[' + content + '](' + href + ')' : content;
      }
      if (['div', 'p', 'section', 'article', 'ul', 'ol', 'blockquote'].includes(tag)) return '\n\n' + content.trim() + '\n\n';
      return content;
    };
    if (element.querySelector('h1,h2,h3,h4,h5,h6,p,ul,ol,pre,table,br')) {
      let fence = null, blank = false;
      const lines = [];
      for (const line of markdown(element).split('\n')) {
        const match = line.match(/^\s*(`{3,}|~{3,})/);
        if (match) {
          const mark = match[1];
          if (fence === null) fence = mark;
          else if (mark[0] === fence[0] && mark.length >= fence.length) fence = null;
          lines.push(line); blank = false;
        } else if (fence !== null || line.trim()) {
          lines.push(line); blank = false;
        } else if (!blank) { lines.push(''); blank = true; }
      }
      return clean(lines.join('\n'));
    }
    return clean(markdown(element) || element.innerText || element.textContent);
  };
  const input = all(cfg.input).find(visible) || null;
  const inputText = (input?.value ?? input?.innerText ?? input?.textContent ?? '').replace(/\u200b/g, '').trim();
  const matched = all(cfg.assistant).filter(el => visible(el) && (!input || !el.contains(input)));
  // Keep a complete outer answer, not its final nested markdown subsection.
  const roots = matched.filter(el => !matched.some(parent => parent !== el && parent.contains(el)));
  // Never fall back to an older completed message while the latest assistant
  // root contains only a still-running reasoning/search panel. Structurally
  // empty virtual-list placeholders are ignored, but a reasoning root remains
  // current because its raw visible text is non-empty.
  const entries = roots.map(el => ({
    el,
    text: answerText(el),
    raw: clean(el.innerText || el.textContent || '')
  })).filter(entry => entry.text || entry.raw);
  const latest = entries.at(-1);
  const text = latest?.text || '';
  // Inspect only this message's controls, never a previous answer's toolbar.
  let scope = latest?.el;
  for (let i = 0; scope && i < 3; i++) {
    const parent = scope.parentElement;
    if (!parent || parent === document.body || parent === document.documentElement ||
        (input && parent.contains(input)) || roots.some(el => el !== latest.el && parent.contains(el))) break;
    scope = parent;
  }
  const controls = scope ? Array.from(scope.querySelectorAll('button,[role="button"],[title],[aria-label]')) : [];
  const label = el => [el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent].filter(Boolean).join(' ').trim();
  const completionText = clean(scope?.textContent || latest?.el?.textContent || '');
  const enabledControls = controls.filter(el => visible(el) && !disabled(el));
  const completedByControl = enabledControls.some(el =>
    /重新生成|重新回答|再次生成|regenerate|retry response/i.test(label(el)));
  const completedByMarker = (cfg.completionMarkers || []).some(marker => completionText.includes(marker));
  const completed = completedByControl || completedByMarker;
  const activeControls = all(['button','[role="button"]']).some(el => visible(el) && !disabled(el) &&
    [el.getAttribute('aria-label'), el.getAttribute('title'), el.textContent].some(value =>
      /^(停止(生成|回答|输出)?|stop( generating| generation| response)?)$/i.test((value || '').trim())));
  const busyStatus = scope && Array.from(scope.querySelectorAll('[role="status"],[aria-busy="true"],[data-state="thinking"],[data-state="streaming"]'))
    .some(el => visible(el) && (el.getAttribute('aria-busy') === 'true' ||
      /^(thinking|streaming)$/.test(el.getAttribute('data-state') || '') ||
      /^(正在思考|思考中|深度思考中|正在搜索|正在生成|thinking|searching)[.。…\s]*$/i.test(el.textContent.trim())));
  let reasoningOnly = false;
  let intermediateOnly = false;
  if (latest) {
    const rawCurrent = clean(latest.el.innerText || latest.el.textContent || '');
    // A few providers briefly mount a plain-text planning/search sentence
    // outside their usual reasoning container.  It is not a final answer even
    // when the site exposes no busy control.  Keep this deliberately narrow so
    // ordinary recommendations such as “我建议……” remain collectable.
    intermediateOnly = !completed && (
      /^(?:(?:我|接下来我)(?:将|会)|首先(?:我将|我会|需要)?|用户(?:的)?(?:问题)?需要)[^。\n]{0,100}(?:搜索|检索|查询|查找|思考|分析|研究)/i.test(text) ||
      /^(?:思考|搜索|检索|查询|分析)(?:草稿|过程|计划|步骤|中|内容)/i.test(text)
    );
    reasoningOnly = !!latest.el.closest(reasoningSelector) || (!text && !!rawCurrent) ||
      (!completed && /^(正在思考中?|深度思考中|思考中|正在搜索|正在检索|thinking|searching)(?:\s|[.。…])/i.test(text)) ||
      intermediateOnly;
  }
  const configuredStop = all(cfg.stop).some(el => visible(el) && !disabled(el));
  const generating = configuredStop || activeControls || !!busyStatus;
  const loginRequired = all(cfg.login || []).some(el => visible(el) && !disabled(el));
  // Hash the entire answer: a same-length edit in the middle also resets stability.
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619) >>> 0;
  return JSON.stringify({
    ok: true,
    text: text,
    count: entries.length,
    signature: String(entries.length) + ':' + String(text.length) + ':' + String(hash),
    generating: generating,
    completed: completed,
    completedByMarker: completedByMarker && !completedByControl,
    completionControlCount: enabledControls.length,
    reasoningOnly: reasoningOnly,
    intermediateOnly: intermediateOnly,
    loginRequired: loginRequired,
    inputText: inputText,
    url: location.href
  });
})();
"""


_FOCUS_INPUT_SCRIPT = r"""
(function () {
  const cfg = __CONFIG__;
  for (const selector of cfg.input) {
    try {
      const input = Array.from(document.querySelectorAll(selector)).find((element) => {
        const style = getComputedStyle(element);
        return style.display !== 'none' && style.visibility !== 'hidden';
      });
      if (!input) continue;
      input.focus();
      if (input.isContentEditable) {
        const range = document.createRange();
        range.selectNodeContents(input);
        range.collapse(false);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      return JSON.stringify({ok:true});
    } catch (_) {}
  }
  return JSON.stringify({ok:false});
})();
"""


_SEND_SCRIPT = r"""
(function () {
  const cfg = __CONFIG__;
  const run = window.__fourAiSendRun = (window.__fourAiSendRun || 0) + 1;
  const visible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden';
  };
  const firstVisible = (selectors) => {
    for (const selector of selectors) {
      try {
        const matches = Array.from(document.querySelectorAll(selector));
        const el = matches.find(visible);
        if (el) return el;
      } catch (_) {}
    }
    return null;
  };
  const inputText = (el) => (el?.value ?? el?.innerText ?? el?.textContent ?? '').replace(/\u200b/g, '').trim();
  const disabled = (el) => Boolean(
    !el || el.disabled || el.getAttribute('aria-disabled') === 'true' ||
    /(^|\s)(disabled|is-disabled)(\s|$)|cursor-not-allowed/i.test(typeof el.className === 'string' ? el.className : '')
  );
  const input = firstVisible(cfg.input);
  if (!input) return JSON.stringify({ok:false, code:'NO_INPUT', detail:'未找到当前站点的聊天输入框，可能尚未登录或网站已改版'});

  input.focus();
  if (input instanceof HTMLTextAreaElement || input instanceof HTMLInputElement) {
    const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(input, cfg.text); else input.value = cfg.text;
    input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:cfg.text}));
    input.dispatchEvent(new Event('change', {bubbles:true}));
  } else {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(input);
    selection.removeAllRanges();
    selection.addRange(range);
    let inserted = false;
    try { inserted = document.execCommand('insertText', false, cfg.text); } catch (_) {}
    // execCommand already emits an input event. Emitting a second event with
    // the complete string duplicates text in Kimi's controlled editor.
    if (!inserted) {
      input.textContent = cfg.text;
      input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText'}));
      input.dispatchEvent(new Event('change', {bubbles:true}));
    }
  }

  const normalizedText = value => value.replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ').replace(/\u200b/g, '').trim();
  if (normalizedText(inputText(input)) !== normalizedText(cfg.text)) {
    return JSON.stringify({ok:false, code:'INPUT_TRUNCATED', detail:'网页输入内容不完整，已阻止发送；请检查网站输入限制'});
  }

  const findButton = (editor) => {
    let button = firstVisible(cfg.send);
    if (!button) {
      const scope = editor.closest('form') || editor.parentElement?.parentElement || document.body;
      const candidates = Array.from(scope.querySelectorAll('button, [role="button"]')).filter(visible);
      button = candidates.find((el) => {
        const hint = [el.innerText, el.textContent, el.getAttribute('aria-label'), el.getAttribute('title'), el.className]
          .filter(Boolean).join(' ');
        return /发送|send|submit|arrow-up|paper-plane/i.test(hint) && !disabled(el);
      }) || null;
    }
    // Icon-only submit buttons often have no stable label. Search progressively
    // larger editor ancestors and prefer their final enabled button.
    if (!button) {
      let scope = editor.parentElement;
      for (let depth = 0; scope && depth < 7; depth += 1, scope = scope.parentElement) {
        const nearby = Array.from(scope.querySelectorAll('button, [role="button"]')).filter(visible);
        const enabled = nearby.filter((el) => !disabled(el));
        if (enabled.length && enabled.length <= 10) button = enabled.at(-1);
        if (button && (nearby.length >= 2 || scope.tagName === 'FORM')) break;
      }
    }
    return button;
  };

  // React and ProseMirror update their submit state asynchronously. Rediscover
  // the editor and button after the input event has propagated instead of
  // clicking a stale, disabled element immediately.
  const submit = (attempt) => {
    if (run !== window.__fourAiSendRun) return;
    const editor = firstVisible(cfg.input) || input;
    if (!inputText(editor)) return;
    // A site's maxlength/contenteditable policy must not silently shorten material.
    if (normalizedText(inputText(editor)) !== normalizedText(cfg.text)) return;
    const button = findButton(editor);
    if (button && !disabled(button)) {
      button.click();
      return;
    }
    if (attempt < 3) {
      window.setTimeout(() => submit(attempt + 1), 220 * (attempt + 1));
      return;
    }
    const form = editor.closest('form');
    if (form && typeof form.requestSubmit === 'function') {
      form.requestSubmit();
      return;
    }
    for (const type of ['keydown', 'keypress', 'keyup']) {
      editor.dispatchEvent(new KeyboardEvent(type, {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true, cancelable:true}));
    }
  };
  submit(0);
  return JSON.stringify({ok:true, code:'SUBMIT_SCHEDULED', detail:input.tagName});
})();
"""


_DIAGNOSTIC_SCRIPT = r"""
(function () {
  const cfg = __CONFIG__;
  const visible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden';
  };
  const count = (selectors) => selectors.map((selector) => {
    try { return {selector:selector, count:document.querySelectorAll(selector).length}; }
    catch (error) { return {selector:selector, count:-1, error:String(error)}; }
  });
  const describe = (selector, limit) => Array.from(document.querySelectorAll(selector))
    .filter(visible).slice(-limit).map((el) => ({
      tag: el.tagName,
      id: el.id || '',
      class: typeof el.className === 'string' ? el.className.slice(0, 240) : '',
      role: el.getAttribute('role') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      testId: el.getAttribute('data-testid') || '',
      placeholder: el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '',
      text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160)
    }));
  return JSON.stringify({
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    input: count(cfg.input),
    send: count(cfg.send),
    assistant: count(cfg.assistant),
    stop: count(cfg.stop),
    login: count(cfg.login || []),
    editorCandidates: describe('textarea, input, [contenteditable="true"], [role="textbox"]', 20),
    buttonCandidates: describe('button, [role="button"]', 35),
    answerCandidates: describe('[data-role], [data-message-role], [data-testid*="message"], [class*="assistant"], [class*="markdown"], [class*="message"]', 50)
  });
})();
"""


SITE_ADAPTERS: tuple[SiteAdapter, ...] = (
    SiteAdapter(
        id="deepseek",
        require_completion_evidence=True,
        name="DeepSeek",
        home_url="https://chat.deepseek.com/",
        input_selectors=("textarea[placeholder*='DeepSeek']", "textarea[placeholder*='发送消息']", "textarea"),
        send_selectors=(".ds-button--primary", "[class*='send-button']", "button[type='submit']"),
        assistant_selectors=(
            ".ds-markdown",
            "[class*='ds-markdown']",
            "[data-role='assistant']",
            "[class*='assistant'] [class*='markdown']",
        ),
        stop_selectors=("[aria-label*='停止']", "[class*='stop-button']", "[data-testid*='stop']"),
        completion_absolute_quiet_seconds=24.0,
    ),
    SiteAdapter(
        id="kimi",
        require_completion_evidence=True,
        name="Kimi",
        home_url="https://www.kimi.com/",
        input_selectors=(
            "[data-testid*='chat-input'] [contenteditable='true']",
            ".chat-input-editor[contenteditable='true']",
            ".chat-input-editor",
            "[role='textbox'][contenteditable='true']",
            "[contenteditable='true']",
        ),
        send_selectors=(
            ".send-button-container:not(.disabled)",
            "button[data-testid*='send']",
            "[class*='send-button']",
            "button[aria-label*='发送']",
            "button[aria-label*='Send']",
        ),
        assistant_selectors=(
            ".segment-assistant .markdown",
            ".segment-assistant",
            "[data-testid*='assistant']",
            "[class*='assistant-content']",
            "[class*='assistant-message']",
            "[data-role='assistant']",
        ),
        stop_selectors=("button[aria-label*='停止']", "[class*='stop-button']", "[data-testid*='stop']"),
        # Kimi's controlled editor is not reliably compatible with bulk
        # execCommand insertion. Use the verified trusted-event path, retaining
        # full input equality checks before Enter (including long report prompts).
        native_input=True,
        # Kimi's final toolbar is commonly a row of unlabelled icon buttons.
        # When neither Stop nor Regenerate exposes an accessible name, accept a
        # substantial answer only after multiple observed versions and a long
        # quiet window. This retains protection against a static search plan.
        completion_fallback_quiet_seconds=18.0,
        completion_fallback_min_chars=160,
        completion_fallback_action_count=3,
        completion_absolute_quiet_seconds=24.0,
    ),
    SiteAdapter(
        id="doubao",
        require_completion_evidence=True,
        name="豆包",
        home_url="https://www.doubao.com/chat/",
        input_selectors=(
            "[data-testid*='chat_input'] [contenteditable='true']",
            "div.tiptap.ProseMirror[contenteditable='true']",
            "div.tiptap.ProseMirror",
            "[role='textbox'][contenteditable='true']",
            "[contenteditable='true']",
            "textarea",
        ),
        send_selectors=(
            "#flow-end-msg-send",
            "[data-testid='chat_input_send_button']",
            "button[aria-label*='发送']",
            "[class*='send-btn']",
            "button[type='submit']",
        ),
        assistant_selectors=(
            ".flow-markdown-body",
            "div[class*='max-w-(--content-max-width)']:not(:has([class*='bg-g-send-msg-bubble-bg']))",
            "[data-testid*='assistant']",
            "[data-testid*='receive_message']",
            "[data-testid*='assistant'] [class*='markdown']",
            "[class*='assistant-message']",
            "[data-role='assistant']",
        ),
        stop_selectors=("button[aria-label*='停止']", "[data-testid*='stop']", "[class*='stop-btn']"),
        # Doubao's current controlled editor can ignore synthetic DOM clicks
        # while leaving the draft visible. Trusted Qt input plus Enter keeps
        # the website's internal state and the submitted text in sync.
        native_input=True,
        completion_absolute_quiet_seconds=24.0,
    ),
    SiteAdapter(
        id="qwen",
        require_completion_evidence=True,
        name="通义千问",
        home_url="https://www.qianwen.com/",
        input_selectors=(
            "[contenteditable='true'][data-placeholder]",
            "[role='textbox'][contenteditable='true']",
            "div[contenteditable='true']",
            "textarea",
        ),
        send_selectors=(
            "button.send-button[aria-label='Send']",
            ".message-input-right-button-send button.send-button",
            "button[type='submit']",
            "[data-testid*='send']",
            "button[aria-label*='发送']",
            "button[aria-label*='Send']",
            "[class*='send-button']",
        ),
        assistant_selectors=(
            "[data-message-role='assistant']",
            "[data-message-author-role='assistant']",
            "[data-role='assistant']",
            "[data-testid*='assistant']",
            "[class*='message-select-wrapper-answer']",
            ".markdown-pc-special-class",
            "#qk-markdown-react",
            ".qk-markdown",
            ".markdown-body",
            "[class*='message-content']",
            "[class*='answer-content']",
            "[class*='assistant'] [class*='markdown']",
        ),
        stop_selectors=("button[aria-label*='停止']", "[data-testid*='stop']", "[class*='stop-button']"),
        # Qwen's controlled editor deliberately ignores synthetic DOM input.
        # Feed it trusted Qt key events so its internal state and send button update.
        native_input=True,
        completion_absolute_quiet_seconds=24.0,
    ),
    SiteAdapter(
        id="yuanbao",
        require_completion_evidence=True,
        name="腾讯元宝",
        home_url="https://yuanbao.tencent.com/",
        input_selectors=(
            "#search-bar .ql-editor[contenteditable='true']",
            ".agent-input-text-area .ql-editor[contenteditable='true']",
            ".ql-editor[contenteditable='true'][data-placeholder]",
            "[role='textbox'][contenteditable='true']",
            "[contenteditable='true']",
        ),
        send_selectors=(
            "#yuanbao-send-btn:not([class*='disabled'])",
            "#yuanbao-send-btn",
            "[aria-label='发送']",
            "[class*='send-btn']",
        ),
        assistant_selectors=(
            "[data-role='assistant']",
            "[data-message-role='assistant']",
            "[class*='agent-chat__conv--assistant']",
            "[class*='agent-chat__conv--ai'] [class*='content']",
            "[class*='hyc-content-text']",
            "[class*='markdown-body']",
        ),
        stop_selectors=(
            "#yuanbao-stop-btn",
            "[aria-label*='停止']",
            "[class*='stop-btn']",
            "[class*='pause-btn']",
        ),
        native_input=True,
        completion_absolute_quiet_seconds=24.0,
        login_selectors=(".agent-dialogue__tool__login",),
    ),
    SiteAdapter(
        id="zhipu",
        require_completion_evidence=True,
        name="智谱清言",
        home_url="https://chatglm.cn/main/alltoolsdetail",
        input_selectors=(
            ".ProseMirror[contenteditable='true']",
            "[contenteditable='true'][data-placeholder]",
            "[role='textbox'][contenteditable='true']",
            "textarea[placeholder]",
            "textarea",
        ),
        send_selectors=(
            "button[data-testid*='send']",
            "button[aria-label*='发送']",
            "button[type='submit']",
            "[class*='send-button']",
            "[class*='send-btn']",
        ),
        assistant_selectors=(
            "[data-role='assistant']",
            "[data-message-role='assistant']",
            "[data-message-author-role='assistant']",
            "[data-testid*='assistant']",
            "[class*='assistant'] [class*='markdown']",
            "[class*='assistant-message']",
            ".markdown-body",
            "[class*='chatglm-markdown']",
        ),
        stop_selectors=(
            "button[aria-label*='停止']",
            "[data-testid*='stop']",
            "[class*='stop-button']",
            "[class*='stop-btn']",
        ),
        native_input=True,
        completion_markers=("思考结束", "已完成思考", "深度思考结束"),
        # The marker can appear before the final prose has finished streaming,
        # while the site's stop control is an unlabelled icon.  A quiet window
        # prevents a short streaming pause from being mistaken for completion.
        completion_quiet_seconds=10.0,
        # ChatGLM occasionally renders every final action as an unlabelled icon,
        # so neither its stop nor regenerate control can be identified.  In that
        # case accept only a substantial answer observed changing repeatedly and
        # then remaining quiet.  This is deliberately slower than marker-based
        # completion and avoids treating a single static search plan as final.
        completion_fallback_quiet_seconds=18.0,
        completion_fallback_min_chars=160,
        completion_fallback_action_count=3,
        completion_absolute_quiet_seconds=24.0,
    ),
)


ADAPTER_BY_ID = {adapter.id: adapter for adapter in SITE_ADAPTERS}
PRIMARY_SITE_IDS = ("deepseek", "kimi", "doubao", "qwen")
BACKUP_SITE_IDS = ("yuanbao", "zhipu")
