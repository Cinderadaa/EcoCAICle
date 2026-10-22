// apps/web/app/result/page.js — Server Component wrapper
import React, { Suspense } from 'react';
import ResultClient from './result';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default function Page() {
  return (
    <Suspense
      fallback={
        <main style={{ minHeight: '60vh', display: 'grid', placeItems: 'center', padding: 16 }}>
          กำลังโหลดผลลัพธ์…
        </main>
      }
    >
      <ResultClient />
    </Suspense>
  );
}