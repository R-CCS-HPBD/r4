/**
 * RiVault MCP Apps - Main Entry Point
 *
 * This module provides interactive UI applications that can be served
 * via MCP's ext-apps SDK and displayed in Claude desktop and other hosts.
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles/global.css';

// Main landing page component
function App() {
  return (
    <div className="mcp-apps-root">
      <h1>RiVault MCP Apps</h1>
      <p>Interactive UI components for the RiVault platform.</p>
    </div>
  );
}

// Only render if there's a root element (standalone mode)
const rootElement = document.getElementById('root');
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

export { App };
