# ALBOS CRM

A lightweight CRM (Customer Relationship Management) system built with **Django**.  
This project allows organisations to manage leads, agents, categories, and follow-ups, with advanced features such as pagination, filtering, search, notifications, and password reset via email.

---

## 🚀 Features

- **User Authentication**
  - Organisors & Agents with different permissions
  - Secure login & signup system

- **Lead Management**
  - Create, update, assign, and delete leads
  - Categories for organizing leads
  - Assign single or multiple leads to agents

- **Search & Filtering**
  - Search leads by:
    - ID  
    - First name  
    - Last name  
    - Email  
    - Phone number  
  - Filter by agent & category
  - Sort by date added or first name

- **Pagination**
  - Dynamic page size (10, 50, 100, 200 per page)
  - Maintains filters and sorting across pages

- **Dashboard**
  - Total leads
  - New leads in the last 30 days
  - Converted leads in the last 30 days

- **Notifications**
  - Real-time notifications for agents when leads are assigned
  - Admin notifications when new leads are created

- **Password Reset**
  - Professional HTML email with reset button
  - Secure token-based reset link

---

## 🛠️ Tech Stack

- **Backend**: Django 5
- **Frontend**: Django Templates + TailwindCSS
- **Database**: SQLite (default), can be swapped with PostgreSQL/MySQL
- **Auth**: Django built-in authentication
- **Email**: Gmail SMTP (can be configured for production)

