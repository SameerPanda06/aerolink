"""Optional: pip install playwright; run with the API on port 8000.

Set CHROMIUM_PATH to use a system Chromium, or install Playwright's browser.
"""
import os
import re
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get('AEROLINK_TEST_URL', 'http://127.0.0.1:8000')


with sync_playwright() as playwright:
    options = {'headless': True}
    if os.environ.get('CHROMIUM_PATH'):
        options['executable_path'] = os.environ['CHROMIUM_PATH']
    browser = playwright.chromium.launch(**options)
    page = browser.new_page(viewport={'width': 1440, 'height': 1080})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(BASE)
    expect(page.locator('#connection')).to_contain_text('API connected')
    assert page.locator('#gravity option').count() == 6
    page.select_option('#gravity', '-x')
    assert page.locator('#gravity').input_value() == '-x'
    baseline = page.request.get(BASE + '/api/dashboard').json()['event_count']
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
    assert page.request.get(BASE + '/api/dashboard').json()['event_count'] == baseline
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
    snapshot = page.request.get(BASE + '/api/dashboard').json()
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
    transfer = snapshot['transfers'][0]
    transfer['status'] = 'receiving'
    transfer['received'] = 2
    transfer['metadata'] = {'mission_id':'NEX-000002','image_id':'IMG-000004','classification':'CLEAR',
        'confidence':.889,'action':'keep','captured_at':'2025-12-28T18:38:11Z',
        'capture_source':'sentinel-2-l2a','bbox':[-118,34,-117,35], 'jpeg_quality':85,
        'original_bytes':69414,'compressed_bytes':57776,'original_dimensions':[512,512],
        'transmitted_dimensions':[512,512],'altitude_m_agl':None,'cloud_cover_percent':.05}
    transfer['timing'] = {'elapsed_seconds':2,'receiving_bytes_per_second':200}
    expect(page.locator('#thumbnail-placeholder')).to_have_text('Receiving image')
    expect(page.locator('#selected-prediction')).to_contain_text('88.9%')
    expect(page.locator('#selected-facts')).to_contain_text('sentinel-2-l2a')
    expect(page.locator('#selected-facts')).to_contain_text('200.0 B/s')
    expect(page.locator('#metadata-download')).to_have_attribute('href','/api/transfers/AABBCCDD/metadata')
    assert page.locator('#selected-thumbnail').is_hidden()
    page.locator('#radial-velocity').fill('1000')
    expect(page.locator('#doppler-result')).to_contain_text('-1444.3 Hz')
    page.locator('#radial-velocity').fill('-1000')
    expect(page.locator('#doppler-result')).to_contain_text('433.001444 MHz')
    telemetry = {'samples':[{'recorded_at':datetime.now(timezone.utc).isoformat(),'source':'pi_http',
        'raw':{'accel_x_g':0,'accel_y_g':0.5,'accel_z_g':0.866,'gyro_x_dps':0,'gyro_y_dps':0,'gyro_z_dps':0},'corrected':None}],
        'calibration':None,'calibration_window':{'fresh_samples':1}}
    page.route('**/api/telemetry?*', lambda route: route.fulfill(json=telemetry))
    expect(page.locator('#tilt-reading')).to_contain_text('Roll 30.0°')
    expect(page.locator('#telemetry-freshness')).to_contain_text('Live')
    assert 'rotate(30.' in page.locator('#attitude-board').get_attribute('transform')
    telemetry['samples'][0]['recorded_at'] = '2020-01-01T00:00:00Z'
    expect(page.locator('#telemetry-freshness')).to_contain_text('Stale')
    page.unroute('**/api/telemetry?*')
    for width in (320,390,768,1440):
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Metadata overflow at {width}px'
    page.unroute('**/api/dashboard')
    page.emulate_media(reduced_motion='reduce')
    expect(page.locator('#motion-toggle')).to_have_text('Motion reduced')
    assert page.locator('#motion-toggle').is_disabled()
    assert page.locator('.beam-travel').evaluate("el => getComputedStyle(el).animationName") == 'none'
    assert page.locator('#transfers').evaluate("el => getComputedStyle(el).opacity") == '1'
    assert not errors, errors
    browser.close()
    print('Browser smoke: data integrity, responsive visuals, pause/reduced motion, chart inspection, navigation and reconnect: PASS')
