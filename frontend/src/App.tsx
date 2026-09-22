import { Navigate, Route, Routes } from "react-router-dom";
import { MetaProvider } from "@/api/MetaContext";
import { AppShell } from "@/features/shell/AppShell";
import { MapSection } from "@/features/map/MapSection";
import { DashboardSection } from "@/features/dashboard/DashboardSection";
import { SkillsSection } from "@/features/skills/SkillsSection";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { CreateLauncher } from "@/features/create/CreateLauncher";

/**
 * Four sections behind one drawer, each owning the whole viewport.
 *
 * The section is in the URL, so a reload lands where you were and a link to the
 * dashboard is a link to the dashboard — neither was true of the old page,
 * which kept the section in a body class.
 */
export function App() {
  return (
    <MetaProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/map" replace />} />
          <Route path="/map" element={<MapSection />} />
          <Route path="/dashboard" element={<DashboardSection />} />
          <Route
            path="/skills"
            element={
              <>
                <SkillsSection />
                <CreateLauncher onCreated={() => window.dispatchEvent(new Event("ml:corpus"))} />
              </>
            }
          />
          <Route path="/settings" element={<SettingsSection />} />
          {/* Anything else is a stale bookmark, not a 404 page worth writing. */}
          <Route path="*" element={<Navigate to="/map" replace />} />
        </Route>
      </Routes>
    </MetaProvider>
  );
}
