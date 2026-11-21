(function () {
    var KEY = 'quizpup_consent';
    var VERSION = 1;
    var CATEGORIES = ['analytics'];
    var listeners = [];

    function read() {
        try {
            var raw = window.localStorage.getItem(KEY);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            return parsed && parsed.v === VERSION ? parsed : null;
        } catch (e) {
            return null;
        }
    }

    function write(choices) {
        var record = { v: VERSION, ts: new Date().toISOString() };
        CATEGORIES.forEach(function (name) { record[name] = !!choices[name]; });
        try { window.localStorage.setItem(KEY, JSON.stringify(record)); } catch (e) {}
        return record;
    }

    function allowed(name) {
        var stored = read();
        return !!(stored && stored[name] === true);
    }

    function activate() {
        var pending = document.querySelectorAll('script[type="text/plain"][data-consent]');
        Array.prototype.forEach.call(pending, function (node) {
            var category = node.getAttribute('data-consent');
            if (!allowed(category) || node.getAttribute('data-consent-active')) return;
            node.setAttribute('data-consent-active', '1');
            var live = document.createElement('script');
            Array.prototype.forEach.call(node.attributes, function (attr) {
                if (attr.name !== 'type' && attr.name !== 'data-consent' && attr.name !== 'data-consent-active') {
                    live.setAttribute(attr.name, attr.value);
                }
            });
            live.async = true;
            node.parentNode.insertBefore(live, node.nextSibling);
        });
    }

    function notify() {
        listeners.forEach(function (fn) { try { fn(); } catch (e) {} });
    }

    function banner() { return document.getElementById('consent-banner'); }

    function show() {
        var el = banner();
        if (el) el.hidden = false;
    }

    function hide() {
        var el = banner();
        if (el) el.hidden = true;
    }

    function choose(grantOptional) {
        var choices = {};
        CATEGORIES.forEach(function (name) { choices[name] = grantOptional; });
        write(choices);
        hide();
        activate();
        notify();
    }

    window.QuizPupConsent = {
        allowed: allowed,
        onChange: function (fn) { listeners.push(fn); },
        open: show
    };

    function init() {
        var accept = document.getElementById('consent-accept');
        var reject = document.getElementById('consent-reject');
        if (accept) accept.addEventListener('click', function () { choose(true); });
        if (reject) reject.addEventListener('click', function () { choose(false); });
        Array.prototype.forEach.call(document.querySelectorAll('[data-cookie-settings]'), function (btn) {
            btn.addEventListener('click', function () {
                show();
                var first = document.getElementById('consent-reject');
                if (first) first.focus();
            });
        });
        if (!read()) show();
        activate();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();