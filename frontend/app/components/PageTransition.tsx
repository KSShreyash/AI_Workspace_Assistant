'use client'

import type { ReactNode } from 'react'
import { AnimatePresence, MotionConfig, motion } from 'framer-motion'
import { usePathname } from 'next/navigation'

export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname()

  return (
    // reducedMotion="user" makes every Framer animation in the tree respect the
    // OS setting. The CSS media query in globals.css covers CSS transitions but
    // cannot reach JS-driven animation, and the sidebar, buttons, page
    // transitions and the parallax hero are all Framer.
    <MotionConfig reducedMotion="user">
    <AnimatePresence mode="wait">
      <motion.div
        key={pathname}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
    </MotionConfig>
  )
}
