// ==UserScript==
// @name         AstrBot 侧边栏挂载 - 存档点 SavePoint
// @namespace    https://github.com/Lilureihia/astrbot_plugin_savepoint
// @version      1.0.0
// @description  在不修改 AstrBot 核心静态文件的前提下，向 AstrBot WebUI 左侧边栏注入【🔥 存档点】选项卡，点击即可内嵌打开 SavePoint 管理面板。
// @author       白昼夢
// @match        http://localhost:6185/*
// @match        http://127.0.0.1:6185/*
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  const SAVEPOINT_URL = 'http://127.0.0.1:8765';

  function injectSidebarTab() {
    if (document.getElementById('savepoint-sidebar-tab')) return;

    // 寻找 AstrBot Vue 侧边栏导航列表
    const navList = document.querySelector('.v-navigation-drawer .v-list') || document.querySelector('nav ul');
    if (!navList) return;

    const tabItem = document.createElement('div');
    tabItem.id = 'savepoint-sidebar-tab';
    tabItem.style.cssText = `
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 16px;
      margin: 4px 8px;
      border-radius: 8px;
      cursor: pointer;
      color: #ff9d5c;
      font-weight: 600;
      font-size: 14px;
      background: rgba(255, 140, 66, 0.12);
      border: 1px solid rgba(255, 140, 66, 0.35);
      transition: all 0.2s;
    `;
    tabItem.innerHTML = `<span style="font-size:18px;">🔥</span><span>存档点 SavePoint</span>`;

    tabItem.onclick = () => {
      let modal = document.getElementById('savepoint-iframe-Overlay');
      if (modal) {
        modal.style.display = modal.style.display === 'none' ? 'flex' : 'none';
        return;
      }

      modal = document.createElement('div');
      modal.id = 'savepoint-iframe-Overlay';
      modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 240px;
        right: 0;
        bottom: 0;
        background: #0f111a;
        z-index: 9999;
        display: flex;
        flex-direction: column;
      `;

      const closeBar = document.createElement('div');
      closeBar.style.cssText = `
        background: #141724;
        padding: 6px 16px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid #2a2f45;
        color: #8b92b2;
        font-size: 12px;
      `;
      closeBar.innerHTML = `
        <span>🔥 存档点 SavePoint 内嵌工作台 (${SAVEPOINT_URL})</span>
        <button id="close-savepoint-btn" style="background:#262b40;color:#fff;border:1px solid #3b4262;padding:4px 12px;border-radius:6px;cursor:pointer;">返回 AstrBot 面板 ✕</button>
      `;

      const iframe = document.createElement('iframe');
      iframe.src = SAVEPOINT_URL;
      iframe.style.cssText = 'width: 100%; height: 100%; border: none; flex: 1;';

      modal.appendChild(closeBar);
      modal.appendChild(iframe);
      document.body.appendChild(modal);

      document.getElementById('close-savepoint-btn').onclick = () => {
        modal.style.display = 'none';
      };
    };

    navList.appendChild(tabItem);
  }

  setInterval(injectSidebarTab, 1200);
})();
