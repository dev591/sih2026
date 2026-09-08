import { createRoot } from 'react-dom/client';
import App from './App.tsx';

// NOTE: deliberately NOT wrapped in <StrictMode>.
// StrictMode double-invokes effects in development, which mounts the r3f
// Canvas twice — OrbitControls then initialises against a stale instance and
// the engine can come up mirrored (cylinders reading 4-3-2-1) on alternate
// reloads. Harmless in a production build, but an unpredictable default camera
// is a real hazard in a rehearsed four-minute demo, so it goes.
createRoot(document.getElementById('root')!).render(<App />);
