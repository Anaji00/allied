'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { authFetch } from '@/lib/api';

export type LangCode = 'en' | 'zh' | 'vi';
export type LangOption = { code: LangCode; label: string };
type Me = { language: LangCode; options: LangOption[] };

// What the server would offer; used until it answers or if it has no language support.
export const FALLBACK: LangOption[] = [
  { code: 'en', label: 'English' },
  { code: 'zh', label: '中文' },
  { code: 'vi', label: 'Tiếng Việt' },
];

const NAV_LABELS: Record<Exclude<LangCode, 'en'>, Record<string, string>> = {
  vi: {
    '/mine': 'Việc của tôi',
    '/inbox': 'Hôm nay',
    '/carriers': 'Giá cước',
    '/sales/pipeline': 'Hao mòn & báo giá',
    '/sales/leads': 'Khách tiềm năng',
    '/compliance/vault': 'Chứng chỉ vật liệu',
    '/alerts': 'Người dùng & cảnh báo',
    '/health': 'Hệ thống',
    '/operations': 'Sage & nhập dữ liệu',
    '/admin/audit': 'Nhật ký hoạt động',
    '/orders': 'Đơn hàng',
    '/rfqs': 'Báo giá (RFQ)',
    '/vendors': 'Nhà cung cấp',
    '/shipments': 'Vận chuyển',
    '/projects': 'Dự án',
    '/ball': 'Đang chờ ai',
    '/jobs': 'Công việc',
    '/': 'Bảng việc',
    '/leads': 'Khách tiềm năng',
    '/assistant': 'Trợ lý',
  },
  zh: {
    '/mine': '我的待办',
    '/inbox': '今日',
    '/carriers': '运费价格',
    '/sales/pipeline': '磨损与报价',
    '/sales/leads': '潜在客户',
    '/compliance/vault': '材质证书',
    '/alerts': '人员与提醒',
    '/health': '系统',
    '/operations': 'Sage 与导入',
    '/admin/audit': '审计记录',
    '/orders': '订单',
    '/rfqs': '询价 (RFQ)',
    '/vendors': '供应商',
    '/shipments': '发货',
    '/projects': '项目',
    '/ball': '待办责任人',
    '/jobs': '工单',
    '/': '看板',
    '/leads': '潜在客户',
    '/assistant': '助手',
  },
};

const SECTION_LABELS: Record<Exclude<LangCode, 'en'>, Record<string, string>> = {
  vi: { 'Daily work': 'Công việc hằng ngày', 'Sales & quality': 'Bán hàng & chất lượng', Admin: 'Quản trị' },
  zh: { 'Daily work': '日常工作', 'Sales & quality': '销售与质量', Admin: '管理' },
};
const TODAY_TABS: Record<Exclude<LangCode, 'en'>, Record<string, string>> = {
  vi: { '/inbox': 'Hộp thư', '/mine': 'Việc của tôi', '/ball': 'Đang chờ ai' },
  zh: { '/inbox': '收件箱', '/mine': '我的待办', '/ball': '待办责任人' },
};

/** A menu group's name in the person's language. */
export function sectionLabel(section: string, lang: LangCode): string {
  return lang === 'en' ? section : (SECTION_LABELS[lang]?.[section] ?? section);
}

/** The Today tabs in the person's language. */
export function todayTab(href: string, fallback: string, lang: LangCode): string {
  return lang === 'en' ? fallback : (TODAY_TABS[lang]?.[href] ?? fallback);
}

export function navLabel(href: string, fallback: string, lang: LangCode): string {
  if (lang === 'en') return fallback;
  return NAV_LABELS[lang]?.[href] ?? fallback;
}

/** The signed-in person's language, and a way to change it. Everyone defaults to English. */
export function useLanguage() {
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ['language', 'me'],
    queryFn: async (): Promise<Me | null> => {
      const res = await authFetch('/api/v1/language/me', { cache: 'no-store' });
      return res.ok ? ((await res.json()) as Me) : null;
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
  const set = useMutation({
    mutationFn: async (language: LangCode) => {
      const res = await authFetch('/api/v1/language/me', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language }),
      });
      if (!res.ok) throw new Error('That didn’t save.');
      return language;
    },
    onSuccess: () => {
      toast.success('Language changed', {
        description: 'The menu, comments and forms follow it. Page titles and buttons are still in English.',
      });
      // Comments and forms are translated for the reader, so they load again in the new language.
      void client.invalidateQueries({ queryKey: ['language'] });
      void client.invalidateQueries({ queryKey: ['updates'] });
      void client.invalidateQueries({ queryKey: ['forms'] });
    },
    onError: (e) => toast.error('The language wasn’t changed', { description: e.message }),
  });
  return {
    available: q.data != null,
    language: (q.data?.language ?? 'en') as LangCode,
    options: q.data?.options?.length ? q.data.options : FALLBACK,
    set: (l: LangCode) => set.mutate(l),
    saving: set.isPending,
  };
}
