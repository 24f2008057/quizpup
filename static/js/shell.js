(function () {
    var root = document.querySelector('[data-account]');
    if (!root) return;
    var btn = root.querySelector('.account-btn');
    var menu = root.querySelector('.account-menu');
    var items = function () {
        return Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
    };

    function isOpen() {
        return !menu.hidden;
    }

    function open(focusIndex) {
        menu.hidden = false;
        btn.setAttribute('aria-expanded', 'true');
        var list = items();
        if (focusIndex === 'last') list[list.length - 1].focus();
        else if (focusIndex === 'first') list[0].focus();
    }

    function close(returnFocus) {
        if (!isOpen()) return;
        menu.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
        if (returnFocus) btn.focus();
    }

    btn.addEventListener('click', function () {
        if (isOpen()) close(false);
        else open();
    });

    btn.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            open('last');
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            open('first');
        }
    });

    menu.addEventListener('keydown', function (e) {
        var list = items();
        var i = list.indexOf(document.activeElement);
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            list[(i + 1) % list.length].focus();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            list[(i - 1 + list.length) % list.length].focus();
        } else if (e.key === 'Home') {
            e.preventDefault();
            list[0].focus();
        } else if (e.key === 'End') {
            e.preventDefault();
            list[list.length - 1].focus();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            close(true);
        } else if (e.key === 'Tab') {
            close(false);
        }
    });

    menu.addEventListener('click', function (e) {
        var item = e.target.closest('[role="menuitem"]');
        if (!item) return;
        if (item.hasAttribute('data-logout')) {
            try { localStorage.removeItem('isLoggedIn'); } catch (err) {}
        }
        if (item.tagName === 'BUTTON') close(false);
    });

    document.addEventListener('click', function (e) {
        if (isOpen() && !root.contains(e.target)) close(false);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && isOpen() && !menu.contains(document.activeElement)) close(true);
    });
})();