/**
 * Password strength widget — SAP-style rules.
 * Usage:
 *   const ps = initPasswordStrength(passwordInput, containerEl, getUsernameFn);
 *   ps.validate()  → true if all rules pass
 *   ps.destroy()   → remove listeners
 */
(function (global) {
    'use strict';

    const RULES = [
        {
            id: 'len',
            label: 'Minimum 8 characters',
            test: pw => pw.length >= 8,
        },
        {
            id: 'upper',
            label: 'At least one uppercase letter (A–Z)',
            test: pw => /[A-Z]/.test(pw),
        },
        {
            id: 'lower',
            label: 'At least one lowercase letter (a–z)',
            test: pw => /[a-z]/.test(pw),
        },
        {
            id: 'digit',
            label: 'At least one number (0–9)',
            test: pw => /[0-9]/.test(pw),
        },
        {
            id: 'special',
            label: 'At least one special character (!@#$%…)',
            test: pw => /[^A-Za-z0-9]/.test(pw),
        },
        {
            id: 'repeat',
            label: 'No 3 or more consecutive identical characters',
            test: pw => !/(.)\1{2,}/.test(pw),
        },
        {
            id: 'username',
            label: 'Does not contain username',
            test: (pw, un) => !un || !pw.toLowerCase().includes(un.toLowerCase()),
        },
    ];

    function score(pw, username) {
        let s = 0;
        RULES.forEach(r => { if (r.test(pw, username)) s++; });
        if (pw.length >= 12) s++;
        return s; // 0 – 8
    }

    function level(s) {
        if (s <= 2) return { label: 'Weak',   color: '#e53e3e', bars: 1 };
        if (s <= 4) return { label: 'Fair',   color: '#dd6b20', bars: 2 };
        if (s <= 6) return { label: 'Good',   color: '#d69e2e', bars: 3 };
        return          { label: 'Strong', color: '#38a169', bars: 4 };
    }

    function initPasswordStrength(inputEl, containerEl, getUsernameFn) {
        getUsernameFn = getUsernameFn || (() => '');

        // Build widget HTML
        containerEl.innerHTML = `
            <div class="ps-bars" style="display:flex;gap:4px;margin:8px 0 6px;">
                <div class="ps-bar" data-bar="1" style="height:4px;flex:1;border-radius:2px;background:#e2e8f0;transition:background .3s;"></div>
                <div class="ps-bar" data-bar="2" style="height:4px;flex:1;border-radius:2px;background:#e2e8f0;transition:background .3s;"></div>
                <div class="ps-bar" data-bar="3" style="height:4px;flex:1;border-radius:2px;background:#e2e8f0;transition:background .3s;"></div>
                <div class="ps-bar" data-bar="4" style="height:4px;flex:1;border-radius:2px;background:#e2e8f0;transition:background .3s;"></div>
            </div>
            <div class="ps-label" style="font-size:11px;font-weight:600;margin-bottom:8px;height:14px;"></div>
            <ul class="ps-rules" style="list-style:none;padding:0;margin:0;">
                ${RULES.map(r => `
                    <li data-rule="${r.id}" style="font-size:11px;display:flex;align-items:center;gap:5px;margin-bottom:3px;color:#718096;">
                        <span class="ps-icon" style="font-size:12px;width:14px;text-align:center;">○</span>
                        <span>${r.label}</span>
                    </li>
                `).join('')}
            </ul>
        `;

        const bars  = containerEl.querySelectorAll('.ps-bar');
        const label = containerEl.querySelector('.ps-label');
        const items = containerEl.querySelectorAll('li[data-rule]');

        function update() {
            const pw = inputEl.value;
            const un = getUsernameFn();
            if (!pw) {
                bars.forEach(b => b.style.background = '#e2e8f0');
                label.textContent = '';
                items.forEach(li => {
                    li.classList.remove('ps-pass', 'ps-fail');
                    li.style.color = '';
                    li.querySelector('.ps-icon').textContent = '○';
                });
                return;
            }
            const s = score(pw, un);
            const lv = level(s);

            bars.forEach((b, i) => {
                b.style.background = i < lv.bars ? lv.color : '#e2e8f0';
            });
            label.textContent  = lv.label;
            label.style.color  = lv.color;

            RULES.forEach(r => {
                const ok = r.test(pw, un);
                const li = containerEl.querySelector(`li[data-rule="${r.id}"]`);
                if (!li) return;
                li.classList.toggle('ps-pass', ok);
                li.classList.toggle('ps-fail', !ok);
                li.style.color = '';
                li.querySelector('.ps-icon').textContent = ok ? '✓' : '✗';
            });
        }

        inputEl.addEventListener('input', update);

        return {
            validate() {
                const pw = inputEl.value;
                const un = getUsernameFn();
                return RULES.every(r => r.test(pw, un));
            },
            update,
            destroy() {
                inputEl.removeEventListener('input', update);
                containerEl.innerHTML = '';
            }
        };
    }

    // Inject base CSS once
    if (!document.getElementById('ps-style')) {
        const s = document.createElement('style');
        s.id = 'ps-style';
        s.textContent = `
            .ps-rules li { color: #718096; transition: color .2s; }
            .ps-rules li.ps-pass { color: #276749; }
            .ps-rules li.ps-fail { color: #c53030; }
        `;
        document.head.appendChild(s);
    }

    global.initPasswordStrength = initPasswordStrength;
})(window);
