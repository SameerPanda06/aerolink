"""Optional: pip install playwright; run with the API on port 8000.

Set CHROMIUM_PATH to use a system Chromium, or install Playwright's browser.
"""
import os
import re
from io import BytesIO
from PIL import Image

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
    # Backend-verified JPEG availability controls the viewer. Browser fixtures
    # exercise the UI without inserting synthetic records into the live database.
    photo = BytesIO()
    Image.new('RGB',(64,48),(112,158,181)).save(photo,format='JPEG')
    snapshot['transfers'] = [{'transfer_id':'AABBCCDD','bytes':31078,'total':156,
        'received':156,'status':'verified','observed_chunks':list(range(1,157)),
        'radio':{'rssi_dbm':-42,'snr_db':9.5,'packet_bytes':95,'payload_bytes':87,
                 'samples':160,'rssi_mean':-43,'snr_mean':9.2,'rssi_min':-47,
                 'rssi_max':-40,'observed_frame_bytes':34000},
        'image':{'sha256':'aabbccdd'+'0'*56,'url':'/api/transfers/AABBCCDD/image'}}]
    page.route('**/api/transfers/AABBCCDD/image',lambda route: route.fulfill(content_type='image/jpeg',body=photo.getvalue()))
    page.route('**/api/dashboard',lambda route: route.fulfill(json=snapshot))
    expect(page.locator('#signal-value')).to_have_text('-42 dBm')
    expect(page.locator('#radio-frame')).to_have_text('95 bytes')
    expect(page.locator('#radio-snr')).to_have_text('9.5 dB')
    expect(page.locator('#image-state')).to_have_text('Server SHA-256 verified')
    expect(page.locator('#received-image')).to_be_visible()
    page.wait_for_function('document.getElementById("received-image").naturalWidth === 64')
    page.locator('#image-open').click()
    expect(page.locator('#image-dialog')).to_be_visible()
    expect(page.locator('#image-dialog-title')).to_contain_text('AABBCCDD')
    page.keyboard.press('Escape')
    expect(page.locator('#image-dialog')).not_to_be_visible()
    assert page.locator('#image-download').get_attribute('href').endswith('?download=true')
    for width in (320,390,768,1440):
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Ledger overflow at {width}px'
    snapshot['transfers'][0]['image'] = None
    snapshot['transfers'][0]['radio'] = None
    expect(page.locator('#image-open')).to_be_disabled()
    expect(page.locator('#received-image')).not_to_be_visible()
    expect(page.locator('#signal-label')).to_have_text('No recorded radio measurements')
    page.unroute('**/api/dashboard')
    page.emulate_media(reduced_motion='reduce')
    expect(page.locator('#motion-toggle')).to_have_text('Motion reduced')
    assert page.locator('#motion-toggle').is_disabled()
    assert page.locator('.beam-travel').evaluate("el => getComputedStyle(el).animationName") == 'none'
    assert page.locator('#transfers').evaluate("el => getComputedStyle(el).opacity") == '1'
    assert not errors, errors
    browser.close()
    print('Browser smoke: data integrity, responsive visuals, pause/reduced motion, chart inspection, navigation and reconnect: PASS')
