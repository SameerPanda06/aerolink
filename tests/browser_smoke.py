"""Optional: pip install playwright; run with the API on port 8000.

Set CHROMIUM_PATH to use a system Chromium, or install Playwright's browser.
"""
import os
import re

from playwright.sync_api import expect, sync_playwright


with sync_playwright() as playwright:
    options = {'headless': True}
    if os.environ.get('CHROMIUM_PATH'):
        options['executable_path'] = os.environ['CHROMIUM_PATH']
    browser = playwright.chromium.launch(**options)
    page = browser.new_page(viewport={'width': 1440, 'height': 1080})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8000')
    expect(page.locator('#connection')).to_contain_text('API connected')
    assert page.locator('#gravity option').count() == 6
    page.select_option('#gravity', '-x')
    assert page.locator('#gravity').input_value() == '-x'
    baseline = page.request.get('http://127.0.0.1:8000/api/dashboard').json()['event_count']
    page.get_by_role('button', name='Explore demo').click()
    expect(page.locator('#event-count')).to_have_text('162')
    assert page.locator('#notice').is_visible()
    assert page.locator('.packet.received').count() == 156
    assert page.locator('#calibrate').is_disabled()
    expect(page.locator('#transfer-percent')).to_have_text('100%')
    expect(page.locator('#transfer-phase')).to_have_text('CHECKSUM VERIFIED')
    expect(page.locator('#science-clear')).to_have_text('1')
    assert page.locator('#sample-position').is_enabled()
    page.locator('#sample-position').fill('0')
    expect(page.locator('#sample-detail')).to_contain_text('Sample 1/70')
    page.get_by_role('button', name='Pause motion').click()
    assert page.locator('#motion-toggle').get_attribute('aria-pressed') == 'true'
    assert page.locator('.beam-travel').evaluate("el => getComputedStyle(el).animationPlayState") == 'paused'
    page.get_by_role('button', name='Resume motion').click()
    page.locator('nav a[href="#classification"]').click()
    expect(page.locator('nav a[href="#classification"]')).to_have_class('active')
    for panel in page.locator('.panel').all():
        panel.scroll_into_view_if_needed()
        expect(panel).to_have_class(re.compile('.*is-visible.*'))
    page.locator('.intro').scroll_into_view_if_needed()
    assert page.request.get('http://127.0.0.1:8000/api/dashboard').json()['event_count'] == baseline
    page.screenshot(path='/tmp/aerolink-desktop.png', full_page=True, animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    for width in (320, 768, 1024):
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Overflow at {width}px'
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#activity').scroll_into_view_if_needed()
    page.locator('.intro').scroll_into_view_if_needed()
    page.screenshot(path='/tmp/aerolink-mobile.png', full_page=True, animations='disabled')
    page.get_by_role('button', name='Return to live').click()
    expect(page.locator('#connection')).to_contain_text('API connected')
    assert page.locator('#event-count').inner_text() == str(baseline)
    page.route('**/api/dashboard', lambda route: route.abort())
    expect(page.locator('#connection')).to_contain_text('API offline')
    assert page.locator('#notice').is_visible()
    page.unroute('**/api/dashboard')
    expect(page.locator('#connection')).to_contain_text('API connected')
    assert not page.locator('#notice').is_visible()
    # A real API-shaped report must render as text, without treating metadata as HTML.
    snapshot = page.request.get('http://127.0.0.1:8000/api/dashboard').json()
    snapshot['classifications'] = [{'image_id':'<b>IMG-TEST</b>','classification':'CLOUDY',
                                    'confidence':.4151,'recommended_action':'defer','source':'pi_http'}]
    page.route('**/api/dashboard', lambda route: route.fulfill(json=snapshot))
    expect(page.locator('#classification-rows')).to_contain_text('41.5%')
    expect(page.locator('#classification-rows')).to_contain_text('Unlinked · Pi HTTP')
    assert page.locator('#classification-rows b').count() == 0
    expect(page.locator('#science-cloudy')).to_have_text('1')
    page.unroute('**/api/dashboard')
    page.emulate_media(reduced_motion='reduce')
    expect(page.locator('#motion-toggle')).to_have_text('Motion reduced')
    assert page.locator('#motion-toggle').is_disabled()
    assert page.locator('.beam-travel').evaluate("el => getComputedStyle(el).animationName") == 'none'
    assert page.locator('#transfers').evaluate("el => getComputedStyle(el).opacity") == '1'
    assert not errors, errors
    browser.close()
    print('Browser smoke: data integrity, responsive visuals, pause/reduced motion, chart inspection, navigation and reconnect: PASS')
