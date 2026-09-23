var webFrame = (function ($) {
    "use strict";
    var self = {};
    self.setUp = function (fiddle) {
        self.setUrl(fiddle.start_url);
        self.navigateTo(fiddle, fiddle.start_url);
        self.setUpNavBar(fiddle.start_url);
    };
    self.setUrl = function (url) {
        $('[name="current_url"]').attr('aria-label', 'Preview URL').val(url);
    };
    self.getUrl = function () {
        return $('[name="current_url"]').val();
    };

    self.getPath = function (url) {
        url = url.substring(1);
        url = url.substring(url.indexOf('/') + 1);
        return url;
    };
    self.setUpNavBar = function (current_url) {
        var $navbar = $('[name="current_url"]');
        var previous_url = current_url;
        var change = function (evt) {
            var url = $(evt.target).val();
            if (previous_url !== url) {
                self.navigateTo(fiddle.getSavedFiddle(), url);
                previous_url = url;
            }
        };
        $navbar.on('blur', change);
        $navbar.keyup(function (e) {
            if (e.keyCode == 13) {
                change(e);
            }
        });
    };

    self.busyTries = 0;
    self.navigateTo = function (fiddle, url, isRetry) {
        if (!isRetry) {
            self.busyTries = 0;
        }
        self.lastNav = {fiddle: fiddle, url: url};
        document.cookie = 'wf_ed=1; path=/; max-age=2592000; SameSite=Lax; Secure';
        if (typeof url == 'undefined') {
            url = self.getUrl();
        }
        self.$frame = $('#web-frame');
        //TODO catch network error?

        self.$frame.html('<div id="web-iframe-loading" class="web-iframe-loading"><i class="web-bigspinner fa fa-spinner fa-spin"></i> </div>' +
            '<iframe id="web-iframe" name="web-iframe" title="Live preview of your fiddle" class="web-iframe" sandbox="allow-same-origin allow-scripts allow-forms allow-pointer-lock"' +
            ' onLoad="webFrame.onFrameLoad(this)" ' +

            'src="/' + fiddle.title + '-' + fiddle.id + '/' + webutils.removeProtocol(url) + '"></iframe>');
    };

    self.reload = function () {
        $('#web-iframe-loading').css({'display': ''});
        frames['web-iframe'].window.location.reload();
    };

    self.isBusyPage = function (iframe) {
        try {
            var body = iframe.contentDocument && iframe.contentDocument.body;
            return !!body && (body.textContent || '').length < 200 && /^Proxy temporarily busy|^Upstream fetch capacity|temporarily exhausted/i.test((body.textContent || '').trim());
        } catch (e) {
            return false;
        }
    };
    self.showBusy = function (final) {
        var msg = final ?
            'The preview proxy is very busy right now. Your code is saved; try the preview again in a minute.' :
            'The preview proxy is busy. Retrying automatically (' + self.busyTries + ')...';
        $('#wf-busy').remove();
        self.$frame.append('<div id="wf-busy" class="wf-busy" role="status"><p>' + msg + '</p>' +
            (final ? '<button type="button" id="wf-busy-retry">Retry preview</button>' : '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i>') + '</div>');
        $('#wf-busy-retry').on('click', function () {
            self.navigateTo(self.lastNav.fiddle, self.lastNav.url);
        });
    };

    self.onFrameLoad = function (iframe) {
        if (self.isBusyPage(iframe)) {
            $('#web-iframe-loading').hide();
            self.busyTries++;
            if (self.busyTries <= 8) {
                self.showBusy(false);
                window.setTimeout(function () {
                    self.navigateTo(self.lastNav.fiddle, self.lastNav.url, true);
                }, Math.min(1000 * self.busyTries, 5000));
            } else {
                self.showBusy(true);
            }
            return;
        }
        self.busyTries = 0;
        $('#wf-busy').remove();
        webFrame.setUrl(webFrame.getPath(iframe.contentWindow.location.pathname) +
            iframe.contentWindow.location.search + iframe.contentWindow.location.hash);
        $('#web-iframe-loading').hide();
    };

    self.setCSS = function (css) {
        frames['web-iframe'].window.document.getElementById('webfiddle-css').innerHTML = css;
    };


    return self;
})(jQuery);
