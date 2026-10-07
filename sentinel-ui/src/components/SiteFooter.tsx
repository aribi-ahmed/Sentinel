import { ArrowUpRight, Github, Linkedin } from 'lucide-react';
import { WelyneMark } from './WelyneBrand';
import './SiteFooter.css';

const YEAR = new Date().getFullYear();

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="container footer-inner">
        <div className="footer-brand">
          <a
            className="footer-welyne focus-ring"
            href="https://welyne.com"
            target="_blank"
            rel="noopener noreferrer"
          >
            <WelyneMark size={26} />
            <span>Welyne</span>
          </a>
          <p className="footer-tagline">
            SENTINEL — multi-agent enterprise risk evaluation. Built during a software engineering
            internship at Welyne.
          </p>
        </div>

        <div className="footer-author">
          <div className="footer-label">Designed &amp; engineered by</div>
          <div className="footer-name">Ahmed Aribi</div>

          <a
            className="footer-portfolio focus-ring"
            href="https://aribi-ahmed.vercel.app"
            target="_blank"
            rel="noopener noreferrer"
            data-testid="link-portfolio"
          >
            <span>View portfolio</span>
            <ArrowUpRight size={14} strokeWidth={2} />
          </a>

          <div className="footer-socials">
            <a
              className="footer-social focus-ring"
              href="https://github.com/aribi-ahmed"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="GitHub"
            >
              <Github size={15} strokeWidth={1.7} />
            </a>
            <a
              className="footer-social focus-ring"
              href="https://www.linkedin.com/in/ahmed-aribi/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="LinkedIn"
            >
              <Linkedin size={15} strokeWidth={1.7} />
            </a>
          </div>
        </div>
      </div>

      <div className="container footer-base">
        <span>© {YEAR} Ahmed Aribi · All rights reserved</span>
        <span className="footer-stack">React · TypeScript · FastAPI · LangGraph · PostgreSQL</span>
      </div>
    </footer>
  );
}
