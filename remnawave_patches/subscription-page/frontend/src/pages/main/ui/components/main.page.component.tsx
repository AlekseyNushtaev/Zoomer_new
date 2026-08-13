/**
 * Замена для: remnawave/subscription-page → frontend/src/pages/main/ui/components/main.page.component.tsx
 *
 * Zoomer: POST на {api}/api/v1/sub_page/pay/{fk_sbp|fk_card|stars|cryptobot}
 * с телом { user_id, duration }. Заголовок X-Sub-Page-Api-Key = SUB_PAGE_API_KEY из .env бота.
 *
 * При сборке Vite (префикс VITE_):
 *   VITE_SUB_PAGE_PAY_API_BASE — по умолчанию http://bot.zoomersky.online (если не задан)
 *   VITE_SUB_PAGE_PAY_API_KEY — обязателен (тот же SUB_PAGE_API_KEY, что в .env бота)
 *
 * В /opt/remnawave/.env.sub (или env_file контейнера subscription-page) задайте ключ;
 * при необходимости переопределите базовый URL (https и т.д.).
 */
import { useCallback, useMemo, useState } from 'react'
import {
    Accordion,
    Box,
    Button,
    Card,
    Center,
    Container,
    Group,
    Image,
    Modal,
    SimpleGrid,
    Stack,
    Text,
    Title
} from '@mantine/core'
import { TSubscriptionPagePlatformKey } from '@remnawave/subscription-page-types'

import {
    AccordionBlockRenderer,
    CardsBlockRenderer,
    InstallationGuideConnector,
    MinimalBlockRenderer,
    RawKeysWidget,
    SubscriptionInfoCardsWidget,
    SubscriptionInfoCollapsedWidget,
    SubscriptionInfoExpandedWidget,
    SubscriptionLinkWidget,
    TimelineBlockRenderer
} from '@widgets/main'
import { useAppConfig, useAppConfigStoreActions, useCurrentLang } from '@entities/app-config-store'
import { useSubscription } from '@entities/subscription-info-store'
import { LanguagePicker } from '@shared/ui/language-picker/language-picker.shared'
import { Page, RemnawaveLogo } from '@shared/ui'

const DEFAULT_SUB_PAGE_PAY_API_BASE = 'https://bot.zoomersky.online'

function subPagePayFromBuild(): { apiBase: string; apiKey: string } {
    const rawBase = String(import.meta.env.VITE_SUB_PAGE_PAY_API_BASE ?? '').trim()
    return {
        apiBase: rawBase || DEFAULT_SUB_PAGE_PAY_API_BASE,
        apiKey: String(import.meta.env.VITE_SUB_PAGE_PAY_API_KEY ?? '').trim()
    }
}

type DurationId = '7' | '30' | '90' | '180' | '365' | '5000'
type PayMethodId = 'fk_sbp' | 'fk_card' | 'stars' | 'cryptobot'

const PAY_METHODS: ReadonlyArray<{ id: PayMethodId; label: string }> = [
    { id: 'fk_sbp', label: 'СБП' },
    { id: 'fk_card', label: 'Карты РФ' },
    { id: 'stars', label: 'Telegram Stars' },
    { id: 'cryptobot', label: 'Telegram Cryptobot' }
]

/**
 * user_id: числовая часть username страницы подписки.
 * Снимаются суффиксы _white, затем _10, затем _3 (как в ТЗ для панели).
 */
function parseSubPageUserId(username: string): number | null {
    let base = username.trim()
    if (base.endsWith('_white')) base = base.slice(0, -'_white'.length)
    if (base.endsWith('_10')) base = base.slice(0, -'_10'.length)
    if (base.endsWith('_3')) base = base.slice(0, -'_3'.length)
    if (!/^\d+$/.test(base)) return null
    const n = Number.parseInt(base, 10)
    return Number.isFinite(n) ? n : null
}

function SubscriptionPayBlock({ isMobile }: { isMobile: boolean }) {
    const { user } = useSubscription()
    const isWhiteProfile = user.username.includes('_white')
    const userId = useMemo(() => parseSubPageUserId(user.username), [user.username])
    const payCfg = useMemo(() => subPagePayFromBuild(), [])
    const subscriptionStillActive = useMemo(() => {
        if (user.userStatus !== 'ACTIVE') return false
        if (user.daysLeft == null) return true
        return Number(user.daysLeft) > 0
    }, [user.daysLeft, user.userStatus])

    const [modalOpen, setModalOpen] = useState(false)
    const [pickedDuration, setPickedDuration] = useState<DurationId | null>(null)
    const [busyMethod, setBusyMethod] = useState<PayMethodId | null>(null)
    const [errorText, setErrorText] = useState<string | null>(null)

    const openPay = useCallback((d: DurationId) => {
        setErrorText(null)
        setPickedDuration(d)
        setModalOpen(true)
    }, [])

    const closeModal = useCallback(() => {
        if (busyMethod) return
        setModalOpen(false)
        setPickedDuration(null)
        setErrorText(null)
    }, [busyMethod])

    const submitPay = useCallback(
        async (method: PayMethodId) => {
            if (userId == null || pickedDuration == null) return
            if (!payCfg.apiKey) return
            setBusyMethod(method)
            setErrorText(null)
            const url = `${payCfg.apiBase.replace(/\/$/, '')}/api/v1/sub_page/pay/${method}`
            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Sub-Page-Api-Key': payCfg.apiKey
                    },
                    body: JSON.stringify({ user_id: userId, duration: pickedDuration })
                })
                const data: unknown = await res.json().catch(() => ({}))
                if (!res.ok) {
                    const msg =
                        typeof data === 'object' &&
                        data !== null &&
                        'detail' in data &&
                        typeof (data as { detail?: unknown }).detail === 'string'
                            ? (data as { detail: string }).detail
                            : `Ошибка ${res.status}`
                    setErrorText(msg)
                    return
                }
                const obj = data as { payment_url?: string; bot_url?: string }
                const redirect = obj.payment_url || obj.bot_url
                if (redirect && typeof redirect === 'string') {
                    window.location.assign(redirect)
                    return
                }
                setErrorText('В ответе нет ссылки для перехода')
            } catch {
                setErrorText('Сеть недоступна или сервер не ответил')
            } finally {
                setBusyMethod(null)
            }
        },
        [pickedDuration, payCfg.apiBase, payCfg.apiKey, userId]
    )

    if (isWhiteProfile) {
        return null
    }

    if (!payCfg.apiKey) {
        return (
            <Card p="md" radius="lg" withBorder>
                <Text c="dimmed" size="sm">
                    Оплата: задайте VITE_SUB_PAGE_PAY_API_KEY при сборке фронта (тот же SUB_PAGE_API_KEY,
                    что в .env бота). При необходимости задайте VITE_SUB_PAGE_PAY_API_BASE.
                </Text>
            </Card>
        )
    }

    if (userId == null) {
        return (
            <Card p="md" radius="lg" withBorder>
                <Text c="dimmed" size="sm">
                    Оплата: не удалось определить user_id из имени пользователя подписки.
                </Text>
            </Card>
        )
    }

    const tariffBtn = (label: string, duration: DurationId) => (
        <Button
            fullWidth
            justify="space-between"
            onClick={() => openPay(duration)}
            radius="md"
            size={isMobile ? 'sm' : 'md'}
            variant="light"
        >
            <Text fw={500} size="sm" style={{ textAlign: 'left' }}>
                {label}
            </Text>
        </Button>
    )

    const tariffStack = (
        <Stack gap="sm">
            {tariffBtn('Пробный тариф — 7 дней — 99 ₽', '7')}
            {tariffBtn('30 дней — 299 ₽', '30')}
            {tariffBtn('90 дней — 749 ₽ (выгода −17%)', '90')}
            {tariffBtn('180 дней — 1349 ₽ (выгода −25%)', '180')}
            {tariffBtn('365 дней — 2399 ₽ (выгода −33%)', '365')}
            {tariffBtn('Навсегда — 4990 ₽', '5000')}
        </Stack>
    )

    return (
        <>
            <Accordion
                chevronPosition="right"
                defaultValue={subscriptionStillActive ? undefined : 'pay'}
                key={subscriptionStillActive ? 'pay-collapsed' : 'pay-open'}
                radius="lg"
                variant="contained"
            >
                <Accordion.Item value="pay">
                    <Accordion.Control>
                        <Title c="white" order={5}>
                            Оплата
                        </Title>
                    </Accordion.Control>
                    <Accordion.Panel>{tariffStack}</Accordion.Panel>
                </Accordion.Item>
            </Accordion>

            <Modal
                centered
                onClose={closeModal}
                opened={modalOpen}
                radius="lg"
                title="Выберите способ оплаты"
            >
                <Stack gap="sm">
                    {errorText ? (
                        <Text c="red" size="sm">
                            {errorText}
                        </Text>
                    ) : null}
                    <SimpleGrid cols={1} spacing="xs">
                        {PAY_METHODS.map((m) => (
                            <Button
                                key={m.id}
                                loading={busyMethod === m.id}
                                onClick={() => void submitPay(m.id)}
                                radius="md"
                                variant="filled"
                            >
                                {m.label}
                            </Button>
                        ))}
                    </SimpleGrid>
                    <Button disabled={!!busyMethod} onClick={closeModal} variant="subtle">
                        Отмена
                    </Button>
                </Stack>
            </Modal>
        </>
    )
}

interface IMainPageComponentProps {
    isMobile: boolean
    platform: TSubscriptionPagePlatformKey | undefined
}

const BLOCK_RENDERERS = {
    cards: CardsBlockRenderer,
    timeline: TimelineBlockRenderer,
    accordion: AccordionBlockRenderer,
    minimal: MinimalBlockRenderer
} as const

const SUBSCRIPTION_INFO_BLOCK_RENDERERS = {
    cards: SubscriptionInfoCardsWidget,
    collapsed: SubscriptionInfoCollapsedWidget,
    expanded: SubscriptionInfoExpandedWidget,
    hidden: null
} as const

export const MainPageComponent = ({ isMobile, platform }: IMainPageComponentProps) => {
    const config = useAppConfig()
    const currentLang = useCurrentLang()
    const { setLanguage } = useAppConfigStoreActions()

    const brandName = config.brandingSettings.title
    let hasCustomLogo = !!config.brandingSettings.logoUrl

    if (hasCustomLogo) {
        if (config.brandingSettings.logoUrl.includes('docs.rw')) {
            hasCustomLogo = false
        }
    }

    const hasPlatformApps: Record<TSubscriptionPagePlatformKey, boolean> = {
        ios: Boolean(config.platforms.ios?.apps.length),
        android: Boolean(config.platforms.android?.apps.length),
        linux: Boolean(config.platforms.linux?.apps.length),
        macos: Boolean(config.platforms.macos?.apps.length),
        windows: Boolean(config.platforms.windows?.apps.length),
        androidTV: Boolean(config.platforms.androidTV?.apps.length),
        appleTV: Boolean(config.platforms.appleTV?.apps.length)
    }

    const atLeastOnePlatformApp = Object.values(hasPlatformApps).some((value) => value)

    const SubscriptionInfoBlockRenderer =
        SUBSCRIPTION_INFO_BLOCK_RENDERERS[config.uiConfig.subscriptionInfoBlockType]

    return (
        <Page>
            <Box className="header-wrapper" py="md">
                <Container maw={1200} px={{ base: 'md', sm: 'lg', md: 'xl' }}>
                    <Group justify="space-between">
                        <Group gap="sm" style={{ userSelect: 'none' }} wrap="nowrap">
                            {hasCustomLogo ? (
                                <Image
                                    alt="logo"
                                    fit="contain"
                                    src={config.brandingSettings.logoUrl}
                                    style={{
                                        width: '32px',
                                        height: '32px',
                                        flexShrink: 0
                                    }}
                                />
                            ) : (
                                <RemnawaveLogo c="cyan" size={32} />
                            )}
                            <Title
                                c={hasCustomLogo ? 'white' : 'cyan'}
                                fw={700}
                                order={4}
                                size="lg"
                            >
                                {brandName}
                            </Title>
                        </Group>

                        <SubscriptionLinkWidget
                            hideGetLink={config.baseSettings.hideGetLinkButton}
                            supportUrl={config.brandingSettings.supportUrl}
                        />
                    </Group>
                </Container>
            </Box>

            <Container
                maw={1200}
                px={{ base: 'md', sm: 'lg', md: 'xl' }}
                py="xl"
                style={{ position: 'relative', zIndex: 1 }}
            >
                <Stack gap="xl">
                    {SubscriptionInfoBlockRenderer && (
                        <SubscriptionInfoBlockRenderer isMobile={isMobile} />
                    )}

                    <SubscriptionPayBlock isMobile={isMobile} />

                    {atLeastOnePlatformApp && (
                        <InstallationGuideConnector
                            BlockRenderer={
                                BLOCK_RENDERERS[config.uiConfig.installationGuidesBlockType]
                            }
                            hasPlatformApps={hasPlatformApps}
                            isMobile={isMobile}
                            platform={platform}
                        />
                    )}

                    <RawKeysWidget isMobile={isMobile} />

                    <Center>
                        <LanguagePicker
                            currentLang={currentLang}
                            locales={config.locales}
                            onLanguageChange={setLanguage}
                        />
                    </Center>
                </Stack>
            </Container>
        </Page>
    )
}
