import { Navigate, Route, Routes } from "react-router-dom";
import { MetaProvider } from "@/api/MetaContext";
import { PrefsProvider } from "@/hooks/usePrefs";
import { AppShell } from "@/features/shell/AppShell";
import { MapSection } from "@/features/map/MapSection";
import { DashboardSection } from "@/features/dashboard/DashboardSection";
import { SkillsSection } from "@/features/skills/SkillsSection";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { PeopleSection } from "@/features/people/PeopleSection";
import { CreateLauncher } from "@/features/create/CreateLauncher";

/**
 * Five sections behind one drawer, each owning the whole viewport.
 *
 * The section is in the URL, so a reload lands where you were and a link to the
 * dashboard is a link to the dashboard — neither was true of the old page,
 * which kept the section in a body class.
 *
 * PrefsProvider wraps everything, because the view state it restores belongs to
 * sections that mount and unmount as you navigate: held any lower, it would be
 * re-read on every section change and each section would spend its first render
 * showing a default it is about to replace.
 */
export function App() {
  return (
    <PrefsProvider>
      <MetaProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<Navigate to="/map" replace />} />
            <Route path="/map" element={<MapSection />} />
            <Route path="/dashboard" element={<DashboardSection />} />
            <Route path="/people" element={<PeopleSection />} />
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
    </PrefsProvider>
  );
}
