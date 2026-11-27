# test_admin_extract_flow.py — Behavior tests for the extract-table config UI
# (admin.js §extract-section) backed by GET/PUT /admin/config/targets.
#
# The UI itself is vanilla JS exercised in the browser; these tests cover the
# Python-side contracts the UI depends on plus JS-grep gates that lock the
# function names the UI must expose. Three concern groups:
#
#   1. targets GET→PUT round-trip integration (ASGI): GET returns the editable
#      shape → the UI assembles a PUT body from it → PUT 200 writes the file.
#      Verifies the contract the UI's save flow relies on.
#   2. base_generation guard: two PUTs with the same base_generation → the
#      second is 409 STALE_WRITE (the UI's auto-reload-on-409 path depends on
#      this signal).
#   3. JS grep gates: admin.js contains the functions the UI requires
#      (renderAliasCard / renderRecordPicker / parseUrl + the error-path →
#      card-index mapper). admin.html contains the save/add button ids.
#
# Strategy mirrors test_config_targets_api.py:
#   - conftest pins legacy mode; dynamic-mode tests swap app.state.settings +
#     rebuild the registry mock.
#   - httpx.AsyncClient(transport=ASGITransport(app), base_url="http://testserver")
#     with the lifespan async context manager run directly.

from __future__ import annotations

import re
import os
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.config import get_settings
from app.main import app, lifespan
from app.target_registry import (
    FeishuTargetConfig,
    TargetRegistry,
    TargetRegistrySnapshot,
)

ADMIN_TOKEN = "test-admin-token"
TEST_BASE_URL = "http://testserver"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "app" / "static"

# Optional real-DOM harness. Uses an existing host Playwright installation;
# no project dependency or production-only testing seam is introduced.
# 可选真实 DOM 测试，复用主机已有 Playwright，不增加项目依赖。
_BOOK_DOM_SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.FEISHU_UI_PLAYWRIGHT_MODULE);
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const requests = [];
  const scenario=process.env.FEISHU_UI_SCENARIO;
  let holdInspect = false;
  let releaseInspect;
  const inspectGate = new Promise(resolve=>{releaseInspect=resolve;});
  let inspectStarted;
  const inspectPending = new Promise(resolve=>{inspectStarted=resolve;});
  let generation = 1;
  let targets = ['2026','2025'].map(alias => ({alias,year:Number(alias),
    app_token:'extract'+alias,table_id:'tbl'+alias,record_id:'rec'+alias,
    enabled:true,original_field_name:'原始信息',
    bill_app_token:'bill'+alias,bill_table_id:'billTbl'+alias}));
  const root = process.env.FEISHU_UI_ROOT;
  const capture = async name => {
    if (process.env.FEISHU_UI_CAPTURE_DIR)
      await page.screenshot({path:process.env.FEISHU_UI_CAPTURE_DIR+'/'+name+'.png'});
  };
  try {
    await page.route('**/*', async route => {
      const request = route.request();
      const u = new URL(request.url());
      assert.equal(u.origin, 'http://task4.local', 'Unexpected external request');
      const payload = request.postDataJSON();
      requests.push({path:u.pathname,query:u.search,method:request.method(),body:payload});
      if(u.pathname === '/admin/ai') return route.fulfill({contentType:'text/html',
        body:fs.readFileSync(root+'/app/static/admin.html','utf8')});
      if(u.pathname.startsWith('/admin/ai/assets/')) {
        const name=u.pathname.split('/').pop();
        return route.fulfill({contentType:name.endsWith('.js')?'application/javascript':
          name.endsWith('.css')?'text/css':'image/svg+xml',
          body:fs.readFileSync(root+'/app/static/'+name)});
      }
      let body;
      if(u.pathname==='/admin/config/targets') {
        if(request.method()==='PUT' && scenario==='server-error') return route.fulfill({status:422,
          contentType:'application/json',body:JSON.stringify({detail:{errors:[
            {path:'targets[1].bill_table_id',message:'table unavailable'}]}})});
        if(request.method()==='PUT' && scenario==='stale') return route.fulfill({status:409,
          contentType:'application/json',body:JSON.stringify({detail:{error:{code:'STALE_WRITE'}}})});
        if(request.method()==='PUT') {targets=payload.targets;generation++;}
        body=request.method()==='PUT'?{success:true,generation}:
          {targets,default_alias:'2026',generation};
      } else if(u.pathname==='/admin/config/profile') body={generation:1,
        prompt_header:'shared prompt',summary_field:'精简原始数据',
        bill:{app_token:'global',table_id:'tblGlobal'},fields:[
          {ai_key:'amount',feishu_field:'金额',type:'number',target:'bill',prompt:'saved amount'}]};
      else if(u.pathname==='/admin/ai/profile' || u.pathname==='/admin/ai/test') {
        const alias=(payload && payload.book_alias)||u.searchParams.get('book_alias')||'2026';
        if(holdInspect && alias==='2026' && u.pathname==='/admin/ai/profile') {
          inspectStarted(); await inspectGate;
        }
        const target=targets.find(t=>t.alias===alias);
        body={book_alias:alias,registry:{generation:1,config_valid:true},provider:'openai',
          model:'fake',profile:{fields:[]},whitelists:{'<img src=x onerror=alert(1)>':['safe']},
          bill_destination:{app_token:target.bill_app_token||'global',
            table_id:target.bill_table_id||'tblGlobal',source:target.bill_app_token?'target':'profile'},
          ai_status:'succeeded',extracted:{amount:42},bill_fields:{金额:42}};
      } else if(u.pathname==='/admin/config/env') body={ai:{ai_model:'fake'},other:{},env_file:'fake.env'};
      else if(u.pathname==='/admin/ai/history') body={items:[],enabled:true};
      else if(u.pathname==='/admin/feishu/fields') body={fields:[
        {name:'精简原始数据',type:'text'},{name:'金额',type:'number'}]};
      else if(u.pathname==='/admin/feishu/tables') body={tables:[
        {table_id:'tblGlobal',name:'Global'}, {table_id:'picked',name:'Picked bill table'}]};
      else if(u.pathname==='/admin/feishu/parse-url') body={app_token:'parsedBill',table_id:'picked'};
      else throw new Error('Unmocked endpoint '+u.pathname);
      await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    await page.addInitScript(() => {
      localStorage.setItem('admin_ai_token','mock-token');
      localStorage.setItem('admin_ai_lang','en');
    });
    page.on('dialog', dialog => {console.log('Dialog:',dialog.type());return dialog.accept();});
    page.on('pageerror', error => console.error('Page error:',error.message));
    await page.goto('http://task4.local/admin/ai');
    await page.locator('.alias-card').first().waitFor();
    await page.waitForFunction(()=>document.querySelector('#bill-fields-list .prompt-input'));
    const cards = page.locator('.alias-card');
    const first=cards.nth(0), second=cards.nth(1);
    const saves=()=>requests.filter(r=>r.path==='/admin/config/targets'&&r.method==='PUT');
    const save=async()=>{
      const response=page.waitForResponse(r=>r.url().endsWith('/admin/config/targets')&&r.request().method()==='PUT');
      await page.locator('#save-targets-btn').click(); await response;
      await page.waitForFunction(()=>!document.querySelector('#save-targets-btn').disabled);
    };
    if(process.env.FEISHU_UI_CAPTURE_BASELINE) {
      await page.locator('#extract-section').scrollIntoViewIfNeeded();
      await capture('task4-before-desktop');
      await page.setViewportSize({width:390,height:844});
      await page.locator('#extract-section').scrollIntoViewIfNeeded();
      await capture('task4-before-mobile');
      await page.setViewportSize({width:1440,height:1000});
    }
    assert.equal(await first.evaluate(e=>e.open),false,'Existing aliases stay collapsed');
    await first.locator('summary').click(); await second.locator('summary').click();
    if(scenario==='pairs') {
      await first.locator('.bill-app-token-input').fill(' editedA ');
      await first.locator('.bill-table-id-input').fill(' tableA ');
      await second.locator('.bill-app-token-input').fill('editedB');
      await second.locator('.bill-table-id-input').fill('tableB');
      await save();
      assert.deepEqual(saves().at(-1).body.targets.map(t=>[t.bill_app_token,t.bill_table_id]),
        [['editedA','tableA'],['editedB','tableB']]);
      await page.reload(); await first.locator('summary').click();
      assert.equal(await first.locator('.bill-app-token-input').inputValue(),'editedA');
      assert.equal(await second.locator('.bill-table-id-input').inputValue(),'tableB');
    } else if(scenario==='dirty-pending') {
      let releaseSave, saveStarted;
      const saveGate=new Promise(resolve=>{releaseSave=resolve;});
      const pendingSave=new Promise(resolve=>{saveStarted=resolve;});
      await page.route('**/admin/config/targets',async route=>{
        if(route.request().method()==='PUT') {saveStarted(); await saveGate;}
        await route.fallback();
      });
      await first.locator('.bill-app-token-input').fill('submittedBill');
      const response=page.waitForResponse(r=>r.url().endsWith('/admin/config/targets')&&r.request().method()==='PUT');
      await page.locator('#save-targets-btn').click(); await pendingSave;
      await first.locator('.bill-app-token-input').fill('newer draft');
      releaseSave(); await response;
      await page.waitForFunction(()=>!document.querySelector('#save-targets-btn').disabled);
      const dialogs=[];
      page.on('dialog',dialog=>dialogs.push(dialog.type()));
      await page.locator('#test-prompt-btn').click();
      await page.waitForFunction(()=>!document.querySelector('#test-prompt-btn').disabled);
      assert.equal(saves().at(-1).body.targets[0].bill_app_token,'submittedBill');
      assert.equal(await first.locator('.bill-app-token-input').inputValue(),'newer draft');
      assert.deepEqual(dialogs,['confirm'],'A completed save must retain edits made while pending');
    } else if(scenario==='dirty-save') {
      const dialogs=[];
      page.on('dialog', dialog=>dialogs.push(dialog.type()));
      const test=async()=>{
        await page.locator('#test-prompt-btn').click();
        await page.waitForFunction(()=>!document.querySelector('#test-prompt-btn').disabled);
      };
      const saveSection=async scope=>{
        const selector=scope==='profile'?'#save-profile-btn':'#save-env-btn';
        const response=page.waitForResponse(r=>r.url().endsWith('/admin/config/'+scope)&&r.request().method()==='PUT');
        await page.locator(selector).click(); await response;
        await page.waitForFunction(s=>!document.querySelector(s).disabled,selector);
      };
      await page.locator('#prompt-header').fill('unsaved shared prompt');
      await test();
      assert.deepEqual(dialogs,['confirm'],'Control: unsaved prompt must trigger confirmation');
      await first.locator('.bill-app-token-input').fill('savedBill');
      await save();
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('savedBill'));
      await test();
      assert.equal(await page.locator('#prompt-header').inputValue(),'unsaved shared prompt');
      assert.equal(requests.filter(r=>r.path==='/admin/config/profile'&&r.method==='PUT').length,0);
      assert.deepEqual(dialogs,['confirm','confirm'],
        'Saving targets must retain the unsaved shared-prompt dirty warning');
      await saveSection('profile'); await test();
      assert.equal(dialogs.length,2,'Saving the last dirty section clears the warning');
      await first.locator('.bill-app-token-input').fill('unsaved target');
      await saveSection('profile'); await test();
      assert.equal(dialogs.length,3,'Saving profile must retain unsaved targets');
      await save(); await test();
      assert.equal(dialogs.length,3,'Saving targets clears its own dirty state');
      await page.locator('#ai-model-input').fill('unsaved model');
      await save(); await test();
      assert.equal(dialogs.length,4,'Saving targets must retain unsaved env');
      await saveSection('env'); await test();
      assert.equal(dialogs.length,4,'Saving env clears its own dirty state');
      await page.locator('#prompt-header').fill('another unsaved prompt');
      await saveSection('env'); await test();
      assert.equal(dialogs.length,5,'Saving env must retain unsaved profile');
    } else if(scenario==='inherit') {
      await first.locator('.bill-mode-select').selectOption('profile');
      await first.locator('summary').click(); await first.locator('summary').click();
      await first.locator('.bill-mode-select').selectOption('target');
      assert.equal(await first.locator('.bill-app-token-input').inputValue(),'bill2026');
      await first.locator('.bill-mode-select').selectOption('profile');
      await save();
      assert.equal(Object.hasOwn(saves().at(-1).body.targets[0],'bill_app_token'),false);
      assert.equal(Object.hasOwn(saves().at(-1).body.targets[0],'bill_table_id'),false);
    } else if(scenario==='partial') {
      await first.locator('.bill-table-id-input').fill('');
      await first.locator('summary').click();
      await page.locator('#save-targets-btn').click();
      assert.equal(saves().length,0);
      assert.equal(await first.evaluate(e=>e.open),true);
      assert.equal(await first.locator('.bill-table-id-input').getAttribute('aria-invalid'),'true');
      assert.match(await page.locator('#extract-banner').textContent(),/app_token.*table_id/);
    } else if(scenario==='selection') {
      assert.equal(await page.locator('#preview-book-select').inputValue(),'2026');
      await first.locator('.bill-app-token-input').fill('unsaved');
      await page.locator('#prompt-header').fill('unsaved shared prompt');
      await page.locator('[data-feishu-field="金额"] .prompt-input').fill('unsaved field prompt');
      const focusedPrompt=await page.locator('[data-feishu-field="金额"] .prompt-input').boundingBox();
      const promptFrame=await page.locator('.bill-table-wrap').boundingBox();
      assert.ok(focusedPrompt.y>=promptFrame.y &&
        focusedPrompt.y+focusedPrompt.height<=promptFrame.y+promptFrame.height,
        'Last-row focused prompt must stay inside the visible table frame');
      if(process.env.FEISHU_UI_CAPTURE_DIR)
        await page.locator('.bill-table-wrap').screenshot({path:process.env.FEISHU_UI_CAPTURE_DIR+'/task4-shared-prompt-focused.png'});
      await page.locator('#preview-book-select').selectOption('2025');
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('billTbl2025'));
      assert.equal(await page.locator('#profile-panel img').count(),0,'Whitelist names are escaped');
      await page.locator('#test-prompt-btn').click();
      await page.waitForFunction(()=>document.querySelector('#result-panel').textContent.includes('billTbl2025'),null,{timeout:3000});
      assert.equal(requests.filter(r=>r.path==='/admin/ai/test').at(-1).body.book_alias,'2025');
      assert.equal(await first.locator('.bill-app-token-input').inputValue(),'unsaved');
      assert.equal(await page.locator('#prompt-header').inputValue(),'unsaved shared prompt');
      assert.equal(await page.locator('[data-feishu-field="金额"] .prompt-input').inputValue(),'unsaved field prompt');
      if(process.env.FEISHU_UI_CAPTURE_DIR) {
        await page.locator('#profile-panel').screenshot({path:process.env.FEISHU_UI_CAPTURE_DIR+'/task4-inspection.png'});
        await page.locator('#result-panel').screenshot({path:process.env.FEISHU_UI_CAPTURE_DIR+'/task4-dry-run.png'});
      }
      await page.locator('#lang-toggle-btn').click();
      assert.equal(await first.locator('.bill-mode-select option:checked').textContent(),'独立账单表');
      assert.equal(await first.locator('.bill-app-token-input').inputValue(),'unsaved');
    } else if(scenario==='save-selection') {
      await page.locator('#preview-book-select').selectOption('2025');
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('billTbl2025'));
      await second.locator('.enabled-input').uncheck();
      await save();
      assert.equal(await page.locator('#preview-book-select').inputValue(),'2026');
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('billTbl2026'),null,{timeout:3000});
    } else if(scenario==='404-inspect' || scenario==='404-test') {
      await page.locator('#preview-book-select').selectOption('2025');
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('billTbl2025'));
      await page.evaluate(testRequest=>{
        const baseFetch=window.fetch;
        window.fetch=(url,options)=>{
          if(String(url).includes(testRequest?'/admin/ai/test':'/admin/ai/profile?book_alias=2026')) {
            return Promise.resolve({status:404,ok:false,json:()=>{
              window.pending404=true;
              return new Promise(resolve=>{window.release404=()=>resolve({detail:{error:{code:'AI_DISABLED'}}});});
            }});
          }
          return baseFetch(url,options);
        };
      },scenario==='404-test');
      if(scenario==='404-test') await page.locator('#test-prompt-btn').click();
      else await page.locator('#preview-book-select').selectOption('2026');
      await page.waitForFunction(()=>window.pending404);
      await page.locator('#preview-book-select').selectOption(scenario==='404-test'?'2026':'2025');
      await page.waitForFunction(alias=>document.querySelector('#profile-panel').textContent.includes('billTbl'+alias),scenario==='404-test'?'2026':'2025');
      await page.evaluate(async()=>{window.release404();await Promise.resolve();await Promise.resolve();});
      if(scenario==='404-test') assert.equal(await page.locator('#result-panel .disabled-notice').count(),0);
      else assert.match(await page.locator('#profile-panel').textContent(),/billTbl2025/);
    } else if(scenario==='server-error' || scenario==='stale') {
      await second.locator('.bill-table-id-input').fill('keepDraft');
      await second.locator('summary').click();
      await save();
      assert.equal(await second.locator('.bill-table-id-input').inputValue(),'keepDraft');
      if(scenario==='server-error') {
        assert.equal(await second.evaluate(e=>e.open),true);
        assert.equal(await second.locator('.bill-table-id-input').getAttribute('aria-invalid'),'true');
        assert.match(await page.locator('#extract-banner').textContent(),/table unavailable/);
      } else assert.match(await page.locator('#extract-banner').textContent(),/edits are retained/);
    } else if(scenario==='race') {
      await page.locator('#preview-book-select').selectOption('2025');
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('billTbl2025'));
      holdInspect=true;
      await page.locator('#preview-book-select').selectOption('2026'); await inspectPending;
      await capture('task4-inspect-held');
      await page.locator('#preview-book-select').selectOption('2025');
      await page.waitForFunction(()=>document.querySelector('#profile-panel').textContent.includes('billTbl2025'));
      const oldResponse=page.waitForResponse(r=>r.url().includes('/admin/ai/profile?book_alias=2026'));
      releaseInspect(); await oldResponse;
      assert.match(await page.locator('#profile-panel').textContent(),/billTbl2025/);
      assert.equal(await page.locator('#profile-panel').textContent().then(t=>t.includes('billTbl2026')),false);
    } else if(scenario==='picker') {
      await page.setViewportSize({width:390,height:844});
      await first.locator('.bill-mode-select').focus();
      await page.keyboard.press('Home'); await page.keyboard.press('Enter');
      assert.equal(await first.locator('.bill-override').isVisible(),false);
      await page.keyboard.press('End'); await page.keyboard.press('Enter');
      assert.equal(await first.locator('.bill-override').isVisible(),true);
      await first.locator('.bill-url-input').fill('https://feishu.cn/base/parsedBill?table=picked');
      await first.locator('.bill-override .parse-url-btn').click();
      await page.waitForFunction(()=>document.querySelector('.bill-app-token-input').value==='parsedBill');
      await first.locator('.bill-override .load-tables-btn').click();
      await first.locator('.bill-override .table-select').selectOption('picked');
      assert.equal(await first.locator('.bill-table-id-input').inputValue(),'picked');
      assert.equal(await second.locator('.bill-app-token-input').inputValue(),'bill2025');
    } else throw new Error('Unknown scenario '+scenario);
    await page.setViewportSize({width:1440,height:1000});
    await first.locator('.bill-mode-select').scrollIntoViewIfNeeded();
    await capture('task4-'+scenario+'-desktop');
    await page.setViewportSize({width:390,height:844});
    await first.locator('.bill-mode-select').scrollIntoViewIfNeeded();
    await capture('task4-'+scenario+'-mobile');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile overflow');
    const evidence=JSON.stringify({scenario,requests:requests.filter(r=>!r.path.includes('/assets/'))},null,2);
    if(process.env.FEISHU_UI_CAPTURE_DIR)
      fs.writeFileSync(process.env.FEISHU_UI_CAPTURE_DIR+'/task4-'+scenario+'-requests.json',evidence);
    console.log(evidence);
  } catch(error) {
    console.error(JSON.stringify({requests,state:await page.locator('#result-panel').textContent(),
      selected:await page.locator('#preview-book-select').inputValue()}));
    await capture('task4-'+scenario+'-failure');
    throw error;
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


@pytest.mark.parametrize("scenario", ["pairs", "inherit", "partial", "selection", "picker",
                                     "server-error", "stale", "race", "save-selection",
                                     "404-inspect", "404-test", "dirty-save", "dirty-pending"])
def test_per_book_dom_behavior(scenario: str) -> None:
    """Drive shipped DOM; opt in with the path to an installed Playwright module."""
    if not os.environ.get("FEISHU_UI_PLAYWRIGHT_MODULE"):
        pytest.skip("Set FEISHU_UI_PLAYWRIGHT_MODULE to run the real-browser harness")
    env = dict(os.environ, FEISHU_UI_ROOT=str(_STATIC_DIR.parents[1]),
               FEISHU_UI_SCENARIO=scenario)
    result = subprocess.run(["node", "-e", _BOOK_DOM_SCRIPT], env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


# --- Shared helpers (mirror test_config_targets_api.py) ---


def _targets_toml(alias_a: str = "2026", alias_b: str | None = "2025") -> str:
    lines = [f'default_alias = "{alias_a}"', ""]
    if alias_b is not None:
        lines += [
            f'[targets."{alias_b}"]',
            f"year = {alias_b}",
            f'app_token = "bascn_{alias_b}"',
            f'table_id = "tbl_{alias_b}"',
            f'record_id = "rec_{alias_b}"',
            'original_field_name = "原始信息"',
            "enabled = true",
            "",
        ]
    lines += [
        f'[targets."{alias_a}"]',
        f"year = {alias_a}",
        f'app_token = "bascn_{alias_a}"',
        f'table_id = "tbl_{alias_a}"',
        f'record_id = "rec_{alias_a}"',
        'original_field_name = "原始信息"',
        "enabled = true",
        "",
    ]
    return "\n".join(lines)


def _dynamic_settings(targets_file: Path | None) -> object:
    return replace(
        get_settings(),
        config_reload_token=ADMIN_TOKEN,
        feishu_targets_file=targets_file,
    )


def _snapshot(targets, default_alias, generation=1):
    by_alias = {t.alias: t for t in targets}
    by_year = {t.year: t.alias for t in targets if t.year is not None}
    return TargetRegistrySnapshot(
        mode="dynamic",
        default_alias=default_alias,
        targets_by_alias=by_alias,
        aliases_by_year=by_year,
        loaded_at=0.0,
        source_path="/tmp/feishu-targets.toml",
        source_mtime=0.0,
        generation=generation,
    )


def _mock_registry_healthy(snapshot, targets_file, *, generation=1):
    state = {"generation": generation, "snapshot": snapshot}

    def _describe():
        return {
            "mode": "dynamic",
            "default_alias": state["snapshot"].default_alias,
            "target_count": len(state["snapshot"].targets_by_alias),
            "reload_generation": state["generation"],
            "config_valid": True,
            "last_reload_error": None,
            "source_path": state["snapshot"].source_path,
        }

    def _install(candidate, temporary_file):
        assert candidate.generation == state["generation"]
        temporary_file.replace(targets_file)
        state["generation"] += 1
        state["snapshot"] = replace(candidate, generation=state["generation"])
        return state["snapshot"]

    registry = MagicMock(spec=TargetRegistry)
    registry.describe = MagicMock(side_effect=_describe)
    registry.get_snapshot = MagicMock(side_effect=lambda: state["snapshot"])
    registry.install_candidate = MagicMock(side_effect=_install)
    registry.maybe_reload = MagicMock(return_value=None)
    return registry


# ─── 1. targets GET→PUT round-trip integration ─────────────────────────────


@pytest.fixture
def targets_file(tmp_path):
    path = tmp_path / "feishu-targets.toml"
    path.write_text(_targets_toml(), encoding="utf-8")
    return path


async def test_targets_get_then_put_round_trip_writes_file(targets_file):
    """
    GIVEN a dynamic-mode registry with 2 targets (2025 + 2026, default=2026)
       AND generation=1
    WHEN the UI flow runs: GET /admin/config/targets, then assembles a PUT body
       from the GET response (same targets, base_generation=GET.generation)
    THEN the PUT response status is 200
      AND response.success is True
      AND response.generation == 2 (installed)
      AND the file round-trips: re-parsed TOML contains both targets verbatim
    """
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    t2025 = FeishuTargetConfig("2025", 2025, "bascn_2025", "tbl_2025", "rec_2025", "原始信息", True)
    snap = _snapshot([t2025, t2026], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, targets_file, generation=1)

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            headers = {"X-Admin-Token": ADMIN_TOKEN}

            # Step 1: GET — the UI reads the editable shape.
            get_resp = await client.get("/admin/config/targets", headers=headers)
            assert get_resp.status_code == 200
            get_body = get_resp.json()
            assert get_body["generation"] == 1
            assert get_body["default_alias"] == "2026"
            targets = get_body["targets"]
            assert len(targets) == 2

            # Step 2: the UI assembles a PUT body from the GET response —
            # round-trip the targets verbatim, carrying base_generation from GET.
            put_body = {
                "default_alias": get_body["default_alias"],
                "targets": targets,
                "base_generation": get_body["generation"],
            }
            put_resp = await client.put(
                "/admin/config/targets", json=put_body, headers=headers
            )

    assert put_resp.status_code == 200, put_resp.text
    put_body_resp = put_resp.json()
    assert put_body_resp["success"] is True
    assert put_body_resp["generation"] == 2

    # File round-trips — both targets present, app_tokens preserved.
    written = targets_file.read_text(encoding="utf-8")
    parsed = tomllib.loads(written)
    assert parsed["default_alias"] == "2026"
    assert set(parsed["targets"].keys()) == {"2025", "2026"}
    assert parsed["targets"]["2026"]["app_token"] == "bascn_2026"
    assert parsed["targets"]["2025"]["app_token"] == "bascn_2025"


# ─── 2. base_generation guard (STALE_WRITE on second PUT) ──────────────────


async def test_targets_second_put_same_base_generation_returns_409_stale(targets_file):
    """
    GIVEN a dynamic-mode registry at generation=1
       AND the first PUT (base_generation=1) succeeds, bumping generation to 2
    WHEN a second PUT is sent with the SAME base_generation=1 (stale)
    THEN the second PUT response status is 409
      AND the error code is STALE_WRITE
      (the UI's auto-reload-on-409-STALE_WRITE path depends on this signal)
    """
    t2026 = FeishuTargetConfig("2026", 2026, "bascn_2026", "tbl_2026", "rec_2026", "原始信息", True)
    snap = _snapshot([t2026], default_alias="2026", generation=1)
    registry = _mock_registry_healthy(snap, targets_file, generation=1)

    put_body = {
        "default_alias": "2026",
        "targets": [
            {
                "alias": "2026", "year": 2026, "app_token": "bascn_2026",
                "table_id": "tbl_2026", "record_id": "rec_2026",
                "original_field_name": "原始信息", "enabled": True,
            }
        ],
        "base_generation": 1,
    }

    async with lifespan(app):
        app.state.settings = _dynamic_settings(targets_file=targets_file)
        app.state.target_registry = registry

        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            headers = {"X-Admin-Token": ADMIN_TOKEN}
            first = await client.put("/admin/config/targets", json=put_body, headers=headers)
            second = await client.put("/admin/config/targets", json=put_body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["error"]["code"] == "STALE_WRITE"


# ─── 3. JS grep gates + admin.html id assertions ───────────────────────────


def _read_static(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


def test_admin_js_exposes_required_ui_functions():
    """
    GIVEN app/static/admin.js exists
    WHEN the source is scanned for the function declarations the extract-table
       UI requires
    THEN it contains function declarations for:
      - renderAliasCard  (renders one alias card)
      - renderRecordPicker (renders the record list + load-more)
      - parseUrl (calls POST /admin/feishu/parse-url)
      - the error-path → card-index mapper (parseErrorPath)
    """
    js = _read_static("admin.js")
    required = ["renderAliasCard", "renderRecordPicker", "parseUrl", "parseErrorPath"]
    for name in required:
        # Match `function <name>(` or `function <name> (` — declaration, not call.
        pattern = r"function\s+" + re.escape(name) + r"\s*\("
        assert re.search(pattern, js), f"admin.js missing function declaration: {name}"


def test_admin_js_node_check_passes():
    """
    GIVEN app/static/admin.js exists on disk
    WHEN `node --check app/static/admin.js` is executed
    THEN the exit code is 0 (JS syntax gate — todo 9 added new functions)
    """
    js_path = _STATIC_DIR / "admin.js"
    assert js_path.is_file()
    result = subprocess.run(
        ["node", "--check", str(js_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"node --check failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


async def test_admin_html_extract_section_has_save_and_add_button_ids():
    """
    GIVEN the FastAPI app is running
    WHEN GET /admin/ai is requested
    THEN the extract-section contains id="save-targets-btn" and id="add-alias-btn"
      (the JS wires click handlers to these ids)
    """
    async with lifespan(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url=TEST_BASE_URL
        ) as client:
            response = await client.get("/admin/ai")

    body = response.text
    assert 'id="save-targets-btn"' in body
    assert 'id="add-alias-btn"' in body
    assert 'id="extract-banner"' in body
