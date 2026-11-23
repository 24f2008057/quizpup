(function () {
    var root = document.getElementById('billingRoot');
    if (!root) return;
    var message = document.getElementById('billingMessage');

    function say(text, isError) {
        message.textContent = text;
        message.className = 'text-sm font-medium ' + (isError ? 'text-danger' : 'text-success');
    }

    function postJson(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function (r) {
            return r.json().then(function (data) { return { ok: r.ok, data: data }; });
        });
    }

    function loadCheckout() {
        if (window.Razorpay) return Promise.resolve();
        return new Promise(function (resolve, reject) {
            var el = document.createElement('script');
            el.src = 'https://checkout.razorpay.com/v1/checkout.js';
            el.onload = resolve;
            el.onerror = function () { reject(new Error('load')); };
            document.head.appendChild(el);
        });
    }

    function summary(form) {
        var billing = form.querySelector('input[type=radio]:checked').value;
        var name = form.dataset.name;
        var out = form.querySelector('[data-summary]');
        if (billing === 'monthly') {
            out.textContent = 'You pay ₹' + form.dataset.monthly + ' today and ₹' + form.dataset.monthly + ' every month until you cancel.';
        } else {
            out.textContent = 'You pay ₹' + form.dataset[billing] + ' once for ' + form.dataset[billing + 'Days'] + ' days of ' + name + '. It does not renew.';
        }
        return billing;
    }

    function finish(ok) {
        if (ok) {
            say('Payment received. Updating your plan…', false);
            setTimeout(function () { window.location.reload(); }, 800);
        }
    }

    function poll(attempts) {
        fetch('/billing/status').then(function (r) { return r.json(); }).then(function (d) {
            if (d.active) return finish(true);
            if (attempts > 0) setTimeout(function () { poll(attempts - 1); }, 2000);
            else say('Your payment is still being confirmed. Please check back in a few minutes.', false);
        });
    }

    function startPayment(form, button) {
        var billing = summary(form);
        button.disabled = true;
        say('Preparing your payment…', false);
        postJson('/billing/checkout', { plan: form.dataset.plan, billing: billing }).then(function (res) {
            if (!res.ok) throw new Error(res.data.error || 'Could not start the payment.');
            return loadCheckout().then(function () { return res.data; });
        }).then(function (d) {
            var options = {
                key: d.key_id,
                name: 'QuizPup',
                description: d.description,
                prefill: d.prefill,
                theme: { color: '#6b3a4f' },
                modal: { ondismiss: function () { button.disabled = false; say('', false); } },
                handler: function (response) {
                    say('Confirming your payment…', false);
                    postJson('/billing/verify', response).then(function (r) {
                        if (r.ok && r.data.active) finish(true);
                        else poll(10);
                    }).catch(function () { poll(10); });
                }
            };
            if (d.kind === 'subscription') options.subscription_id = d.subscription_id;
            else { options.order_id = d.order_id; options.amount = d.amount; options.currency = 'INR'; }
            var rz = new window.Razorpay(options);
            rz.on('payment.failed', function () {
                button.disabled = false;
                say('The payment did not go through. You have not been charged for a plan. Please try again.', true);
            });
            rz.open();
        }).catch(function (err) {
            button.disabled = false;
            say(err.message === 'load' ? 'Could not load the payment window. Check your connection and try again.' : err.message, true);
        });
    }

    Array.prototype.forEach.call(root.querySelectorAll('form[data-plan]'), function (form) {
        summary(form);
        form.addEventListener('change', function () { summary(form); });
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            if (root.dataset.ready !== 'true' || root.dataset.adult !== 'true') return;
            startPayment(form, form.querySelector('button[type=submit]'));
        });
    });

    var start = document.getElementById('cancelStart');
    if (start) {
        var confirmBox = document.getElementById('cancelConfirm');
        start.addEventListener('click', function () {
            confirmBox.hidden = false;
            start.hidden = true;
            document.getElementById('cancelYes').focus();
        });
        document.getElementById('cancelNo').addEventListener('click', function () {
            confirmBox.hidden = true;
            start.hidden = false;
            start.focus();
        });
        document.getElementById('cancelYes').addEventListener('click', function () {
            postJson('/billing/cancel').then(function (res) {
                if (!res.ok) return say(res.data.error || 'Could not cancel.', true);
                say('Renewal cancelled. You keep your plan until ' + res.data.ends_on + '.', false);
                setTimeout(function () { window.location.reload(); }, 1200);
            });
        });
    }
})();