import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './components/App';
import { initBridge } from './lib/acp-bridge';
import '../../../packages/r4-design/tokens.css';
import './index.css';

const container = document.getElementById('root');
if (!container) throw new Error('#root container is missing from the webview document');

// The extension host injects the per-view message token as a data attribute.
// Every outbound message must carry it or the host rejects the request.
initBridge(container.dataset.nonce ?? '');

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
