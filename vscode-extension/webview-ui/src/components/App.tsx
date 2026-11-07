import { AppProvider, useApp, useAppDispatch } from '@/state/store';
import ErrorBoundary from './ErrorBoundary';
import TopBar from './TopBar';
import SessionList from './SessionList';
import Transcript from './Transcript';
import Composer from './Composer';
import StatusBar from './StatusBar';
import Studio from './Studio';
import SystemTab from './SystemTab';
import ShellStreamsPanel from './ShellStreamsPanel';
import AskCards from './AskCards';
import SessionDock from './SessionDock';

/** V4 alignment: chat is perennial; Studio/System open inside a detail pane
 *  beside the transcript (vs full-screen tab swap). 'chat' tab id = pane closed. */
function DetailPane() {
  const { tab } = useApp();
  const dispatch = useAppDispatch();
  if (tab !== 'studio' && tab !== 'system') return null;
  const label = tab === 'studio' ? 'Studio' : 'System';
  return (
    <aside className="r4-detail-pane" aria-label={`${label} detail pane`}>
      <div className="r4-detail-header">
        <span className="r4-detail-title">{label}</span>
        <button
          type="button"
          className="r4-detail-close"
          title="Close detail pane (tab: Chat)"
          onClick={() => dispatch({ type: 'ui/tab', tab: 'chat' })}
        >
          ×
        </button>
      </div>
      <div className="r4-detail-body">
        {tab === 'studio' ? <Studio /> : <SystemTab />}
      </div>
    </aside>
  );
}

function MainContent() {
  return (
    <>
      <div className="r4-body">
        <SessionList />
        <Transcript />
        <ShellStreamsPanel />
        <DetailPane />
      </div>
      <SessionDock />
      <AskCards />
      <Composer />
    </>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppProvider>
        <div className="r4-app">
          <TopBar />
          <MainContent />
          <StatusBar />
        </div>
      </AppProvider>
    </ErrorBoundary>
  );
}
