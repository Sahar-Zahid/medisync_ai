import Navbar from '../components/Navbar.jsx'
import Hero from '../components/Hero.jsx'
import TrustSection from '../components/TrustSection.jsx'
import HowItWorks from '../components/HowItWorks.jsx'
import Features from '../components/Features.jsx'
import CallToAction from '../components/CallToAction.jsx'
import Footer from '../components/Footer.jsx'
import useScrollToHash from '../hooks/useScrollToHash.js'

function LandingPage() {
  useScrollToHash()

  return (
    <div className="page">
      <Navbar />
      <main>
        <Hero />
        <TrustSection />
        <HowItWorks />
        <Features />
        <CallToAction />
      </main>
      <Footer />
    </div>
  )
}

export default LandingPage
