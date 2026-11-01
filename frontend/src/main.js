import { createApp } from 'vue'
import App from './App.vue'
import { setLocale, initialLocale } from './i18n'
import './style.css'

// before the first render, so the sign-in page is already in the right one;
// the account's own choice replaces it once /api/auth/me answers
setLocale(initialLocale())
createApp(App).mount('#app')
