import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 개발 중에는 /api 를 그대로 API 서버로 넘긴다. 같은 오리진이 되므로
// 브라우저에 API 주소나 키가 노출되지 않는다. 배포에서는 VITE_API_BASE 를 쓴다.
const API = process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: { '/api': { target: API, changeOrigin: true } },
  },
});
