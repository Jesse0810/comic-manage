import { createRouter, createWebHashHistory } from 'vue-router';
import DashboardPage from '../pages/DashboardPage.vue';
import LibraryPage from '../pages/LibraryPage.vue';
import ScanPage from '../pages/ScanPage.vue';
import MatchPage from '../pages/MatchPage.vue';
import SettingsPage from '../pages/SettingsPage.vue';

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: DashboardPage },
    { path: '/library', component: LibraryPage },
    { path: '/scan', component: ScanPage },
    { path: '/match', component: MatchPage },
    { path: '/settings', component: SettingsPage },
  ],
});
