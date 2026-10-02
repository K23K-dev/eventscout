import {
  BookOpen, Briefcase, CalendarDays, Church, Cpu, Drama, Dumbbell, Film, FlaskConical, Footprints, GraduationCap, HandHeart, HeartPulse,
  Laugh, Mic, Music, Palette, PencilLine, Shapes, Trees, TrendingUp, Trophy, Users, UtensilsCrossed, type LucideIcon,
} from 'lucide-react'

export interface Topic {
  label: string
  icon: LucideIcon
  color: string
}

// Soft tints keep the page neutral; related topics share one.
const tint = { rose: '#f2a7b8', amber: '#f2c46d', lime: '#b8d883', teal: '#77d0bf', sky: '#88c3f1', indigo: '#aab3f6', orange: '#f3a87c', stone: '#cdc6ba' }

// The categories enrichment assigns (the API's Topic values), in the order Discover lists them.
export const topics: Record<string, Topic> = {
  music: { label: 'Music', icon: Music, color: tint.indigo },
  theater: { label: 'Theater', icon: Drama, color: tint.rose },
  'visual art': { label: 'Art', icon: Palette, color: tint.rose },
  'talks and lectures': { label: 'Talks', icon: Mic, color: tint.sky },
  'classes and workshops': { label: 'Workshops', icon: PencilLine, color: tint.sky },
  'family and kids': { label: 'Family', icon: Shapes, color: tint.amber },
  'food and drink': { label: 'Food & drink', icon: UtensilsCrossed, color: tint.orange },
  'outdoors and nature': { label: 'Outdoors', icon: Trees, color: tint.lime },
  comedy: { label: 'Comedy', icon: Laugh, color: tint.amber },
  film: { label: 'Film', icon: Film, color: tint.indigo },
  dance: { label: 'Dance', icon: Footprints, color: tint.rose },
  technology: { label: 'Tech', icon: Cpu, color: tint.teal },
  science: { label: 'Science', icon: FlaskConical, color: tint.teal },
  sports: { label: 'Sports', icon: Trophy, color: tint.orange },
  'fitness and wellness': { label: 'Fitness', icon: Dumbbell, color: tint.orange },
  volunteering: { label: 'Volunteering', icon: HandHeart, color: tint.lime },
  'student life': { label: 'Student life', icon: GraduationCap, color: tint.sky },
  careers: { label: 'Careers', icon: TrendingUp, color: tint.stone },
  'business and networking': { label: 'Networking', icon: Briefcase, color: tint.stone },
  'books and writing': { label: 'Books', icon: BookOpen, color: tint.stone },
  'community and culture': { label: 'Community', icon: Users, color: tint.amber },
  health: { label: 'Health', icon: HeartPulse, color: tint.teal },
  faith: { label: 'Faith', icon: Church, color: tint.stone },
}

const general: Topic = { label: 'Event', icon: CalendarDays, color: tint.stone }

/** How an event looks in lists: its first topic. */
export function topicOf(event: { topics: string[] }): Topic {
  return topics[event.topics[0] ?? ''] ?? general
}
