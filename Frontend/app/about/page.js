import React from 'react';
import Image from 'next/image';

export default function Page() {
  return React.createElement(
    'div', { className: 'about-page' },
    React.createElement('h1', null, '🤔 EcoAICle คืออะไร?'),
    React.createElement('p', { className: 'subtitle' }, 'AI ที่ช่วยบอกคุณว่า “ขยะชิ้นนี้ควรไปถังไหน” แถมยังแนะนำวิธีทำให้ขยะสะอาดพร้อมรีไซเคิล'),
    React.createElement('div', { className: 'card' },
      React.createElement('h2', null, '🛠️ วิธีการใช้งาน'),
      React.createElement('div', { className: 'steps' },
        React.createElement('div', { className: 'step' }, '📸 ', React.createElement('strong', null, 'ถ่ายภาพ'),
          React.createElement('p', null, 'ถ่ายภาพขยะที่ต้องการทิ้ง')
        ),
        React.createElement('div', { className: 'step' }, '🤖 ', React.createElement('strong', null, 'AI ให้คำแนะนำ'),
          React.createElement('p', null, 'ระบบ AI จะระบุประเภทขยะและถังที่เหมาะสม')
        ),
        React.createElement('div', { className: 'step' }, '✅ ', React.createElement('strong', null, 'ทิ้งขยะถูกถัง'),
          React.createElement('p', null, 'ทิ้งตามที่ระบบแนะนำได้เลย')
        )
      )
    ),
    React.createElement('h2', null, '🗑️ ประเภทถังขยะ'),
    React.createElement('div', { className: 'bins' },
      React.createElement('div', { className: 'bin-card blue' },
        React.createElement(Image, { src: '/Blue_Bin.png', alt: 'ขยะทั่วไป', width: 84, height: 84 }),
        React.createElement('h3', null, 'ขยะทั่วไป'),
        React.createElement('p', null, 'ขยะทั่วไปย่อยสลายได้ยาก')
      ),
      React.createElement('div', { className: 'bin-card yellow' },
        React.createElement(Image, { src: '/Yellow_Bin.png', alt: 'ขยะรีไซเคิล', width: 84, height: 84 }),
        React.createElement('h3', null, 'ขยะรีไซเคิล'),
        React.createElement('p', null, 'ขยะที่นำกลับมาใช้ผลิตใหม่ได้')
      ),
      React.createElement('div', { className: 'bin-card green' },
        React.createElement(Image, { src: '/Green_Bin.png', alt: 'ขยะเปียก', width: 84, height: 84 }),
        React.createElement('h3', null, 'ขยะเปียก'),
        React.createElement('p', null, 'ขยะที่ย่อยสลายได้ตามธรรมชาติ')
      )
    ),
    React.createElement('h2', null, '🌱 ประโยชน์ของการแยกขยะ'),
    React.createElement('ul', { className: 'benefits' },
      React.createElement('li', null, '♻️ ลดขยะ: ช่วยลดปริมาณขยะที่ต้องกำจัด'),
      React.createElement('li', null, '🌍 ช่วยสิ่งแวดล้อม: ลดมลพิษและลดการใช้ทรัพยากรใหม่'),
      React.createElement('li', null, '💰 ประหยัด: สร้างมูลค่าจากการรีไซเคิล'),
      React.createElement('li', null, '📘 เรียนรู้: เพิ่มความรู้เรื่องการจัดการขยะ')
    ),
  React.createElement('footer', { className: 'aboutFooter' },
    React.createElement('div', { className: 'footerMain' },
      React.createElement('span', null, 'พัฒนาโดยทีม '),
      React.createElement('strong', { className: 'footerBrand' },
        React.createElement(Image, { src: '/Logo.png', alt: 'EcoAICle Logo', width: 24, height: 24 }),
        'EcoAICle'
      ),
      React.createElement('span', { className: 'muted' }, ' เพื่อสร้างสังคมไร้ขยะอย่างยั่งยืน 🌿')
    ),
    React.createElement('div', { className: 'footerFollow' },
      React.createElement('span', { className: 'emoji' }, '📸'),
      React.createElement('span', null, ' ติดตามพวกเราได้ที่ '),
      React.createElement('a', {
        href: 'https://www.instagram.com/',
        target: '_blank',
        rel: 'noopener noreferrer',
        className: 'footerLink'
      }, '@EcoAICle')
    )
  ),
    React.createElement('style', null, `
      /* About page local styles */
      .about-page {
        width: 100%;
        height: 100dvh;
        overflow: hidden;
        margin: 0 auto;
        padding: clamp(14px, 3vh, 32px) clamp(16px, 3vw, 32px) clamp(20px, 4vh, 56px);
        color: #172859;
      }
      .about-page > h1 {
        margin: 0;
        font-size: clamp(28px, 5.5vh, var(--text-2xl));
        line-height: 1.2;
        text-align: center;
      }
      .about-page > .subtitle {
        width: min(100%, 720px);
        margin: clamp(6px, 1.5vh, 12px) auto clamp(14px, 3vh, 28px);
        color: #506080;
        font-size: clamp(13px, 2.4vh, var(--text-base));
        line-height: 1.35;
        text-align: center;
      }
      .about-page > h2 {
        margin: clamp(14px, 2.5vh, 28px) 0 clamp(8px, 1.5vh, 14px);
        font-size: clamp(22px, 4vh, var(--text-xl));
        line-height: 1.25;
      }
      .about-page .card {
        padding: clamp(12px, 2vh, 24px);
        border-radius: 18px;
        background: #fff;
        box-shadow: 0 10px 28px rgba(50, 84, 176, 0.10);
      }
      .about-page .card h2 { margin: 0 0 clamp(8px, 1.5vh, 16px); font-size: clamp(22px, 4vh, var(--text-xl)); }
      .about-page .steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(8px, 1.5vw, 12px); }
      .about-page .step {
        padding: clamp(8px, 1.5vh, 14px) clamp(10px, 1.5vw, 16px);
        border-radius: 14px;
        background: #f7f9ff;
        color: #172859;
        font-size: clamp(14px, 2.3vh, var(--text-base));
      }
      .about-page .step strong { font-weight: 700; }
      .about-page .step p { margin-top: 4px; color: #506080; font-size: clamp(12px, 1.9vh, var(--text-sm)); line-height: 1.3; }
      .about-page .benefits {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 4px 20px;
        padding-left: 1.4em;
        color: #506080;
        font-size: clamp(13px, 2.2vh, var(--text-base));
        line-height: 1.3;
      }
      .about-page .bins {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: clamp(8px, 1.5vw, 16px);
        width: 100%;
        margin-top: clamp(4px, 1vh, 8px);
      }
      .about-page .bin-card {
        width: 100%;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: clamp(8px, 1.5vh, 16px);
        background: #fff;
        box-shadow: 0 2px 6px rgba(0,0,0,0.05);
        text-align: center;
      }
      .about-page .bin-card h3 {
        margin: 4px 0 2px;
        font-size: clamp(15px, 2.5vh, 23px);
        font-weight: 700;
      }
      .about-page .bin-card img {
        width: clamp(44px, 7vh, 84px);
        height: clamp(44px, 7vh, 84px);
        object-fit: contain;
      }
      .about-page .bin-card p { color: #555; font-size: clamp(12px, 1.8vh, 18px); line-height: 1.3; }
      .about-page .aboutFooter{
        background:#fff;
        border-radius:12px;
        padding: clamp(8px, 1.5vh, 16px);
        margin-top: clamp(12px, 2vh, 24px);
        box-shadow:0 2px 6px rgba(0,0,0,0.06);
        text-align:center;
      }
      .about-page .footerMain{
        display:flex;
        align-items:center;
        justify-content:center;
        flex-wrap:wrap;
        gap:6px;
        font-size: clamp(11px, 1.7vh, 13px);
        line-height:1.3;
        color:#374151;
      }
      .about-page .footerBrand{
        display:inline-flex;
        align-items:center;
        gap:8px;
        font-weight:800;
        color:#1f2937;
      }
      .about-page .muted{ color:#4b5563; }
      .about-page .footerFollow{
        margin-top:4px;
        display:flex;
        align-items:center;
        justify-content:center;
        gap:6px;
        color:#374151;
      }
      .about-page .footerLink{
        color:#2563EB;
        font-weight:700;
        text-decoration:none;
      }
      .about-page .footerLink:hover{ text-decoration:underline; }
      .about-page .emoji{ font-size:15px; }
      @media (max-width:640px){
        .about-page .steps,
        .about-page .bins { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        .about-page .benefits { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .about-page > h1 { font-size: clamp(25px, 5vh, 38px); }
        .about-page > h2,
        .about-page .card h2 { font-size: clamp(20px, 3.5vh, 29px); }
        .about-page .footerMain{ font-size:12px; }
        .about-page .emoji{ font-size:13px; }
      }
    `)
  );
}
