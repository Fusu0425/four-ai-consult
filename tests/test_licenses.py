from tools.collect_licenses import PageText


def test_notice_parser_preserves_license_text_not_scripts():
    page = PageText()
    page.feed('<h1>License</h1><pre>Copyright A &amp; B\n  retain notice</pre><script>hidden()</script>')
    result = ''.join(page.parts)
    assert 'Copyright A & B\n  retain notice' in result
    assert 'hidden()' not in result
