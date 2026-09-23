var main = (function ($) {
    "use strict";
    var self = {};
    var defaultCodeMirrorOptions = {
        gutters: ["note-gutter", "CodeMirror-linenumbers"],
        tabSize: 4,
        indentUnit: 4,
        matchBrackets: true,
        autoCloseBrackets: true,
        lineNumbers: true,
        lineWrapping: true,
        extraKeys: {"Ctrl-Space": "autocomplete"},
//        completeSingle: false,
        tabMode: 'spaces' // or 'shift'
    };

    // Add fallback for currentSavedFiddle
    if (typeof currentSavedFiddle === 'undefined') {
        window.currentSavedFiddle = {
            id: '',
            title: '',
            description: '',
            start_url: '',
            script: '',
            style: '',
            script_language: 'javascript',
            style_language: 'css'
        };
    }

    self.setup = function () {
        window.jsEditor = CodeMirror($('#js-editor')[0], $.extend({
            mode: {name: "javascript", globalVars: true}
        }, defaultCodeMirrorOptions));

        window.cssEditor = CodeMirror($('#css-editor')[0], $.extend({
            mode: "css"

        }, defaultCodeMirrorOptions));

        jsEditor.getInputField().setAttribute('aria-label', 'JavaScript editor');
        cssEditor.getInputField().setAttribute('aria-label', 'CSS editor');
        Inlet(jsEditor);
        Inlet(cssEditor);

        fiddle.setUp(currentSavedFiddle);

        webFrame.setUp(currentSavedFiddle);

        self.addEditorCompletion(jsEditor, 'js');
        self.addEditorCompletion(cssEditor, 'css');


        var setMainHeight = function () {
            var $tip = $('#wf-onboard');
            var mainHeight = $(window).height() - $('.main-header').outerHeight() - ($tip.is(':visible') ? $tip.outerHeight() : 0);
            $('.main-content').height(mainHeight);
        };

        var refreshUI = function () {
            setMainHeight();
            viewport.doLayout();
        };

        $(window).resize(refreshUI);
        window.setTimeout(refreshUI, 2000);
        refreshUI();

        var saveFunction = function (evt, callback) {
            if (typeof callback == 'undefined') {
                callback = function () {
                }
            }
            webutils.setIconLoading('#save');

            var currentFiddle = fiddle.getCurrentFiddle();

            $.ajax({
                url: '/createfiddle',
                data: currentFiddle,
                success: function (data) {

                    if (data === "success") {
                        webutils.setIconDone('#save');
                        fiddle.setSavedFiddle(currentFiddle);
                        history.replaceState({}, 'fiddle', '/' + webutils.urlencode(currentFiddle.title) + '-' + currentFiddle.id);
                        callback()
                    }
                },
                "type": "GET",
                "cache": false,
                "error": function (xhr, error, thrown) {
                    //flash msg

                    if (error == "parsererror") {
                    }
                }
            });
            return false;
        };
        $('#save').on('click', saveFunction);
        $(document).on('keydown', function (event) {
            if (event.which == 83 && event.ctrlKey) {
                event.preventDefault();
                saveFunction();
            }
        });
        var escAttr = function (v) {
            return String(v || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        };
        var showShare = function () {
            var f = fiddle.getSavedFiddle();
            var editorUrl = window.location.origin + '/' + webutils.urlencode(f.title || 'fiddle') + '-' + f.id;
            var fullUrl = window.location.origin + '/' + webutils.urlencode(f.title || 'fiddle') + '-' + f.id + '/' + webutils.removeProtocol(f.start_url || '');
            var text = (f.title ? f.title + ' - ' : '') + 'my remix of ' + webutils.removeProtocol(f.start_url || 'a web page') + ' made with WebFiddle';
            var u = encodeURIComponent(editorUrl), t = encodeURIComponent(text);
            webutils.setModal(
                '<div class="wf-share"><h3>Share your fiddle</h3>' +
                '<label for="wf-share-editor">Editor link (others can see and remix your code)</label>' +
                '<div class="wf-share_row"><input id="wf-share-editor" class="webfiddle-share-link" type="text" readonly value="' + escAttr(editorUrl) + '"><button type="button" class="wf-primary wf-copy" data-target="wf-share-editor">Copy</button></div>' +
                '<label for="wf-share-full">Fullscreen result link</label>' +
                '<div class="wf-share_row"><input id="wf-share-full" class="webfiddle-share-link" type="text" readonly value="' + escAttr(fullUrl) + '"><button type="button" class="wf-copy" data-target="wf-share-full">Copy</button></div>' +
                '<div class="wf-share_socials">' +
                (navigator.share ? '<button type="button" class="wf-primary" id="wf-native-share"><i class="fa fa-share-alt" aria-hidden="true"></i> Share...</button>' : '') +
                '<a class="wf-btn" target="_blank" rel="noopener" href="https://x.com/intent/tweet?url=' + u + '&text=' + t + '"><i class="fa fa-twitter" aria-hidden="true"></i> X / Twitter</a>' +
                '<a class="wf-btn" target="_blank" rel="noopener" href="https://www.facebook.com/sharer/sharer.php?u=' + u + '"><i class="fa fa-facebook" aria-hidden="true"></i> Facebook</a>' +
                '<a class="wf-btn" target="_blank" rel="noopener" href="https://www.reddit.com/submit?url=' + u + '&title=' + t + '"><i class="fa fa-reddit" aria-hidden="true"></i> Reddit</a>' +
                '<a class="wf-btn" target="_blank" rel="noopener" href="https://news.ycombinator.com/submitlink?u=' + u + '&t=' + t + '"><i class="fa fa-hacker-news" aria-hidden="true"></i> Hacker News</a>' +
                '</div><p class="wf-share_note">Links show a preview card with your fiddle title and description, so name your fiddle in the Options panel before sharing.</p></div>'
            );
            $('#wf-native-share').on('click', function () {
                navigator.share({title: f.title || 'WebFiddle', text: text, url: editorUrl}).catch(function () {});
            });
            $('.wf-copy').on('click', function () {
                var $b = $(this), input = document.getElementById($b.data('target'));
                var done = function () {
                    $b.text('Copied!');
                    window.setTimeout(function () { $b.text('Copy'); }, 2000);
                };
                input.select();
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(input.value).then(done, function () { document.execCommand('copy'); done(); });
                } else {
                    document.execCommand('copy');
                    done();
                }
            });
            window.setTimeout(function () {
                $('#wf-share-editor').focus().select();
            }, 200);
        };
        var shareFunction = function (evt) {
            if (evt) evt.preventDefault();
            var shown = false;
            var open = function () {
                if (!shown) {
                    shown = true;
                    showShare();
                }
            };
            saveFunction(evt, open);
            window.setTimeout(open, 5000);
            return false;
        };
        $('#share').on('click', shareFunction);
        var runFunction = function (evt) {
            webutils.setIconLoading('#webfiddle_run');

            saveFunction(evt, function () {
                webutils.setIconDone('#webfiddle_run');

                webFrame.navigateTo(fiddle.getSavedFiddle());
            });
            return false;
        };
        $('#webfiddle_run').on('click', runFunction);
        $(document).on('keydown', function (event) {
            if (event.which == 13 && event.ctrlKey) {
                runFunction();
            }
        });

        var tipKey = 'wf_onboard_seen_v1', $tip = $('#wf-onboard');
        var tipSeen = false;
        try { tipSeen = !!window.localStorage.getItem(tipKey); } catch (e) {}
        var hideTip = function () {
            $tip.attr('hidden', true);
            try { window.localStorage.setItem(tipKey, '1'); } catch (e) {}
            refreshUI();
        };
        if (tipSeen) {
            $tip.attr('hidden', true);
        }
        $('#wf-onboard-close').on('click', hideTip);
        $('#wf-onboard-try').on('click', function () {
            hideTip();
            jsEditor.focus();
            jsEditor.setCursor(jsEditor.lineCount(), 0);
        });
        $('#webfiddle_run, #save, #share').on('click', function () {
            if (!$tip.attr('hidden')) hideTip();
        });

        $('#login-link').on('click', function (evt) {
            evt.preventDefault();
            var html = nunjucks.render('templates/login_form.jinja2', {
                action: '/login',
                submit_label: 'Login',
                login: true
            });
            webutils.setModal(html);
            return false;
        });

        $(document).on('click', '#register-link', function (evt) {
            evt.preventDefault();
            var html = nunjucks.render('templates/login_form.jinja2', {
                action: '/register',
                submit_label: 'Register',
                login: false
            });
            webutils.setModal(html);
            return false;
        });
    };

    self.addEditorCompletion = function (editor, type) {
        var times = 0;
        editor.on('keypress', function (codeMirror, event) {
            var isAlphabetical = event.which >= 97 && event.which <= 122;
            if (isAlphabetical) {
                times++;
            }
            else {
                times = 0;
            }
            var hashKey = (event.which == 35 && event.shiftKey);
            var dotKey = event.which == 46;
            var show = hashKey || dotKey;
            //51 shift  190
            if (times >= 1 || show) {
                editor.showHint({completeSingle: false});
            }
            //INJECT INTO IFRAME
            if (type == 'css') {
                webFrame.setCSS(editor.getValue());
            }
        })
        
        // Also handle keydown for non-printable keys
        editor.on('change', function (codeMirror, event) {
            // Show hint on every keydown
            // editor.showHint({completeSingle: false});

            // INJECT INTO IFRAME
            if (type == 'css') {
                webFrame.setCSS(editor.getValue());
            }
        });
    };


    return self;
})(jQuery);



