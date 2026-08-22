def test_polish_hooks():
    html = open("public/index.html", encoding="utf-8").read()
    assert html.count("</html>") == 1
    for token in ("btn-premium.loading", "@keyframes spin", "skel", "setBtnLoading", "showToast"):
        assert token in html
